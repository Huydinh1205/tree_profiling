# Individual tree segmentation with lidR.
# Usage: Rscript segment_lidr.R input.las out_prefix METHOD WS_A WS_B [CHM_RES] [GROUND]
#   METHOD : dalponte | silva | li2012
#   window size for tree tops: ws = WS_A + WS_B * height  (metres)
#   GROUND : csf (classify ground ourselves, default) | keep (use the file's class 2)
# Writes out_prefix_ids.txt (one treeID per input point, same order) and
#        out_prefix_hag.txt (height above ground per point) and out_prefix_ttops.csv
suppressPackageStartupMessages({library(lidR); library(terra)})
a <- commandArgs(trailingOnly = TRUE)
f <- a[1]; out <- a[2]; method <- a[3]
wsA <- as.numeric(a[4]); wsB <- as.numeric(a[5])
res <- if (length(a) >= 6) as.numeric(a[6]) else 0.5
ground <- if (length(a) >= 7) a[7] else "csf"
set_lidr_threads(2)
t0 <- Sys.time()
las <- readLAS(f, select = "xyzc")
las@data$idx <- seq_len(npoints(las))
if (ground == "csf") {
  las@data$Classification <- 0L
  las <- classify_ground(las, csf(sloop_smooth = FALSE, class_threshold = 0.3, cloth_resolution = 0.5, rigidness = 2L), last_returns = FALSE)
}
nlas <- normalize_height(las, tin())
nlas@data$Z[nlas@data$Z < 0] <- 0
chm <- rasterize_canopy(nlas, res = res, p2r(subcircle = 0.15), pkg = "terra")
chm <- terra::focal(chm, w = 3, fun = median, na.rm = TRUE)   # light smoothing, fills pits
wsf <- function(x) { pmax(1.5, wsA + wsB * x) }
ttops <- locate_trees(chm, lmf(ws = wsf, hmin = 2))
if (method == "dalponte") {
  algo <- dalponte2016(chm, ttops, th_tree = 2, th_seed = 0.45, th_cr = 0.55, max_cr = 20)
} else if (method == "silva") {
  algo <- silva2016(chm, ttops, max_cr_factor = 0.6, exclusion = 0.3)
} else if (method == "li2012") {
  algo <- li2012(dt1 = wsA, dt2 = wsB, R = 2, Zu = 15, hmin = 2, speed_up = 10)
} else stop("unknown method")
seg <- segment_trees(nlas, algo, attribute = "treeID")
ids <- seg@data$treeID; ids[is.na(ids)] <- 0L
# points below 0.5 m (ground, grass) are not tree points
ids[seg@data$Z < 0.5] <- 0L
o <- order(seg@data$idx)
write(ids[o], paste0(out, "_ids.txt"), ncolumns = 1)
write(round(seg@data$Z[o], 3), paste0(out, "_hag.txt"), ncolumns = 1)
if (method != "li2012") write.csv(sf::st_drop_geometry(ttops), paste0(out, "_ttops.csv"), row.names = FALSE)
cat(sprintf("points=%d trees=%d time=%.1fs\n", npoints(las), length(unique(ids[ids > 0])), as.numeric(difftime(Sys.time(), t0, units = "secs"))))
