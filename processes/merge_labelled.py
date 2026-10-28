"""Merge several labelled FOR-instance .las files (e.g. RMIT train + test) into one
labelled file, keeping classification and treeID.

The RMIT train and test files are two parts of ONE plot with ONE tree numbering, and
they overlap a little at the border (same points in both files, one tree in both).
So by default points with identical coordinates are kept once (the copy with a tree
label wins) and tree IDs are kept as they are. Use --shift-ids for files from
different plots whose IDs would collide.

Usage: python merge_labelled.py ../data/RMIT/train.las ../data/RMIT/test.las --out ../data/RMIT/train_test.las
"""
import argparse
import numpy as np, laspy


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shift-ids", action="store_true", help="renumber trees of the 2nd, 3rd... file so IDs never collide")
    ap.add_argument("--keep-duplicates", action="store_true", help="do not remove points with identical coordinates")
    a = ap.parse_args()

    parts, offset, crs = [], 0, None
    for k, f in enumerate(a.files):
        L = laspy.read(f)
        if "treeID" not in L.point_format.extra_dimension_names:
            raise SystemExit(f"{f} has no treeID field")
        t = np.asarray(L.treeID).astype(np.int64)
        n_trees = len(np.unique(t[t > 0]))
        if a.shift_ids:
            t = np.where(t > 0, t + offset, 0); offset = int(t.max())
        print(f"{f}: {len(t):,} points, {n_trees} trees" + (f", IDs shifted to start after {offset - int(t.max()) + 1}" if a.shift_ids and k else ""))
        parts.append((np.asarray(L.x), np.asarray(L.y), np.asarray(L.z), np.asarray(L.classification), t, np.full(len(t), k)))
        crs = crs or L.header.parse_crs()

    x, y, z, c, t, src = (np.concatenate(k) for k in zip(*parts))
    if not a.keep_duplicates:
        key = np.c_[np.round(x, 3), np.round(y, 3), np.round(z, 3)]
        # best copy first: labelled tree point, then not an out-of-plot point, then the later file
        order = np.lexsort((-src, c == 3, t == 0))
        _, first = np.unique(key[order], axis=0, return_index=True)
        keep = np.sort(order[first])
        print(f"duplicates removed: {len(x) - len(keep):,}")
        x, y, z, c, t = x[keep], y[keep], z[keep], c[keep], t[keep]

    hdr = laspy.LasHeader(point_format=6, version="1.4")
    hdr.offsets = [np.floor(x.min()), np.floor(y.min()), np.floor(z.min())]
    hdr.scales = [0.001, 0.001, 0.001]
    hdr.add_extra_dim(laspy.ExtraBytesParams(name="treeID", type=np.int32))
    if crs is not None:
        try: hdr.add_crs(crs)
        except Exception: pass
    W = laspy.LasData(hdr)
    W.x, W.y, W.z = x, y, z
    W.classification = c.astype(np.uint8)
    W.treeID = t.astype(np.int32)
    W.write(a.out)
    print(f"-> {a.out}: {len(x):,} points, {len(np.unique(t[t > 0]))} trees")


if __name__ == "__main__":
    main()
