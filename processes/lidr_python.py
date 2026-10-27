"""Python ports of two lidR tree segmentation algorithms, so they run without R.

Ported line by line from the lidR source (GPL-3, github.com/r-lidar/lidR):
  * lmf()           tree tops, variable window       src/LAS.cpp  LAS::filter_local_maxima
  * dalponte2016()  crown region growing on a CHM     src/C_dalponte2016.cpp
  * li2012()        point-cloud region growing        src/LAS.cpp  LAS::segment_trees

References
  Popescu & Wynne (2004) PE&RS 70(5) 589-604 (variable window tree tops)
  Dalponte & Coomes (2016) Methods Ecol Evol 7, 1236-1245
  Li, Guo, Jakubowski & Kelly (2012) PE&RS 78(1) 75-84

Because lidR is GPL-3, this file is also GPL-3.
The heavy loops use numba (pip install numba); the first call compiles them (~10 s).
"""
import numpy as np
from numba import njit


# --------------------------------------------------------------------------- grid index
def _grid_index(x, y, cell):
    """Points bucketed in square cells: returns order, cell start/end, grid shape and origin."""
    x0, y0 = x.min(), y.min()
    cx = ((x - x0) / cell).astype(np.int64); cy = ((y - y0) / cell).astype(np.int64)
    nx, ny = int(cx.max()) + 1, int(cy.max()) + 1
    key = cy * nx + cx
    order = np.argsort(key, kind="stable").astype(np.int64)
    counts = np.bincount(key, minlength=nx * ny)
    start = np.zeros(nx * ny + 1, np.int64); start[1:] = np.cumsum(counts)
    return order, start, nx, ny, x0, y0


# --------------------------------------------------------------------------- lmf
@njit(cache=True)
def _lmf(x, y, z, ws, fixed_ws, min_height, order, start, nx, ny, x0, y0, cell):
    """LAS::filter_local_maxima (circular window). Points are visited in input order,
    as in lidR; ties go to the point tagged first."""
    n = len(x)
    lm = np.zeros(n, np.bool_)
    state = np.zeros(n, np.int8)          # 1 = known not a local maximum
    for i in range(n):
        if z[i] < min_height:
            state[i] = 1
    for i in range(n):
        if state[i] == 1:
            continue
        hws = ws[i] / 2.0
        hws2 = hws * hws
        zmax = z[i]
        is_lm = True
        cx0 = max(int((x[i] - hws - x0) / cell), 0); cx1 = min(int((x[i] + hws - x0) / cell), nx - 1)
        cy0 = max(int((y[i] - hws - y0) / cell), 0); cy1 = min(int((y[i] + hws - y0) / cell), ny - 1)
        stop = False
        for cy in range(cy0, cy1 + 1):
            if stop: break
            for cx in range(cx0, cx1 + 1):
                if stop: break
                c = cy * nx + cx
                for k in range(start[c], start[c + 1]):
                    j = order[k]
                    dx = x[j] - x[i]; dy = y[j] - y[i]
                    if dx * dx + dy * dy > hws2:
                        continue
                    zj = z[j]
                    if zj > zmax:
                        is_lm = False
                    if fixed_ws and zj < z[i]:
                        state[j] = 1
                    if zj == zmax and lm[j]:
                        is_lm = False
                        stop = True
                        break
        lm[i] = is_lm
    return lm


def lmf(x, y, z, ws_fun=None, ws=None, hmin=2.0):
    """Tree tops: True for points (or CHM cell centres) that are the highest in their window.
    ws_fun(height) -> window diameter (m), or a fixed diameter ws."""
    x = np.ascontiguousarray(x, float); y = np.ascontiguousarray(y, float); z = np.ascontiguousarray(z, float)
    if ws_fun is not None:
        w = np.asarray(ws_fun(z), float).copy(); w[z < hmin] = w.min(); fixed = False
    else:
        w = np.full(len(x), float(ws)); fixed = True
    cell = max(float(w.max()) / 2.0, 0.5)
    order, start, nx, ny, x0, y0 = _grid_index(x, y, cell)
    return _lmf(x, y, z, w, fixed, hmin, order, start, nx, ny, x0, y0, cell)


# --------------------------------------------------------------------------- dalponte2016
@njit(cache=True)
def _dalponte(image, seeds, th_seed, th_crown, th_tree, dist):
    """C_dalponte2016, same loops and conditions. image: CHM (-inf for empty), seeds: tree ids."""
    nrow, ncol = image.shape
    nseed = seeds.max()
    sx = np.zeros(nseed + 1, np.int64); sy = np.zeros(nseed + 1, np.int64)
    sum_h = np.zeros(nseed + 1); npix = np.zeros(nseed + 1, np.int64)
    for i in range(nrow):
        for j in range(ncol):
            s = seeds[i, j]
            if s != 0:
                sx[s] = i; sy[s] = j; sum_h[s] = image[i, j]; npix[s] = 1
    region = seeds.copy(); temp = seeds.copy()
    nb_r = np.array([-1, 0, 0, 1]); nb_c = np.array([0, -1, 1, 0])
    grown = True
    while grown:
        grown = False
        for r in range(1, nrow - 1):
            for k in range(1, ncol - 1):
                if region[r, k] != 0:
                    idx = region[r, k]
                    h_seed = image[sx[idx], sy[idx]]
                    mh_crown = sum_h[idx] / npix[idx]
                    for q in range(4):
                        pr = r + nb_r[q]; pc = k + nb_c[q]; pz = image[pr, pc]
                        if pz > th_tree:
                            if (pz > h_seed * th_seed and pz > mh_crown * th_crown and
                                    pz <= h_seed + h_seed * 0.05 and
                                    abs(sx[idx] - pr) < dist and abs(sy[idx] - pc) < dist and
                                    region[pr, pc] == 0):
                                temp[pr, pc] = region[r, k]
                                npix[idx] += 1
                                sum_h[idx] += image[pr, pc]
                                grown = True
        region[:, :] = temp
    return region


def dalponte2016(chm, seeds, th_tree=2.0, th_seed=0.45, th_cr=0.55, max_cr=10):
    """chm: 2D array (NaN for empty cells); seeds: 2D int array, tree id at tree-top cells.
    max_cr is in pixels, as in lidR."""
    img = np.where(np.isnan(chm), -np.inf, chm).astype(float)
    # lidR hands the C++ code a matrix with rows = x (west to east) and columns = y
    # (south to north). The growing order depends on it, so use the same layout.
    to_lidr = lambda a: np.ascontiguousarray(a[::-1, :].T)
    out = _dalponte(to_lidr(img), to_lidr(seeds.astype(np.int64)), th_seed, th_cr, th_tree, max_cr)
    return np.ascontiguousarray(out.T[::-1, :])


# --------------------------------------------------------------------------- li2012
@njit(cache=True)
def _li2012(x, y, z, is_lm, dt1, dt2, zu, th_tree, radius):
    """LAS::segment_trees (Li et al. 2012) with lidR's speed-up radius. z = height above ground."""
    ni = len(x)
    idtree = np.zeros(ni, np.int64)                  # 0 = no tree (lidR: NA)
    radius = radius * radius; dt1 = dt1 * dt1; dt2 = dt2 * dt2
    U = np.argsort(-z, kind="stable")               # highest first
    n = ni; k = 1
    xmin = x.min(); ymin = y.min()
    Px = np.empty(ni); Py = np.empty(ni); Nx = np.empty(ni); Ny = np.empty(ni)
    while n > 0:
        u = U[0]
        inN = np.zeros(n, np.bool_)
        if z[u] >= th_tree:
            np_ = 0; nn = 0
            Px[0] = x[u]; Py[0] = y[u]; np_ = 1
            idtree[u] = k
            Nx[0] = xmin - 100; Ny[0] = ymin - 100; nn = 1          # dummy point
            ux = x[u]; uy = y[u]
            for i in range(1, n):
                v = U[i]
                dxu = x[v] - ux; dyu = y[v] - uy
                if dxu * dxu + dyu * dyu > radius:
                    inN[i] = True
                    continue
                dmin1 = 1e300
                for a in range(np_):
                    dx = Px[a] - x[v]; dy = Py[a] - y[v]; d = dx * dx + dy * dy
                    if d < dmin1: dmin1 = d
                dmin2 = 1e300
                for a in range(nn):
                    dx = Nx[a] - x[v]; dy = Ny[a] - y[v]; d = dx * dx + dy * dy
                    if d < dmin2: dmin2 = d
                dt = dt2 if z[v] > zu else dt1
                if is_lm[v]:
                    if dmin1 > dt or (dmin1 < dt and dmin1 > dmin2):
                        inN[i] = True; Nx[nn] = x[v]; Ny[nn] = y[v]; nn += 1
                    else:
                        Px[np_] = x[v]; Py[np_] = y[v]; np_ += 1; idtree[v] = k
                else:
                    if dmin1 <= dmin2:
                        Px[np_] = x[v]; Py[np_] = y[v]; np_ += 1; idtree[v] = k
                    else:
                        inN[i] = True; Nx[nn] = x[v]; Ny[nn] = y[v]; nn += 1
        # keep the points in N for the next tree
        m = 0
        for i in range(n):
            if inN[i]:
                U[m] = U[i]; m += 1
        n = m
        k += 1
    return idtree


def li2012(x, y, hag, dt1=1.5, dt2=2.0, R=2.0, Zu=15.0, hmin=2.0, speed_up=10.0):
    """Tree ID per point (0 = none). Same defaults as lidR::li2012()."""
    x = np.ascontiguousarray(x, float); y = np.ascontiguousarray(y, float); z = np.ascontiguousarray(hag, float)
    is_lm = lmf(x, y, z, ws=R, hmin=0.0) if R > 0 else np.ones(len(x), bool)
    return _li2012(x, y, z, is_lm, dt1, dt2, Zu, hmin, speed_up)
