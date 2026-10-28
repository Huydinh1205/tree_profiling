# RMIT plot, FOR-instance dataset

Source: FOR-instance (Puliti et al. 2023), Zenodo record 8287792, licence CC BY 4.0.
Drone LiDAR (Riegl miniVUX-1UAV, about 490 points/m2) over a dry eucalypt forest in Australia,
every tree labelled by hand (`treeID`) with the FOR-instance classes
(1 low vegetation, 2 ground, 3 out-of-plot, 4 stem, 5 live branches, 6 woody branches).

| File | Points | Labelled trees | Note |
|---|---|---|---|
| `train.laz` | 1,483,208 | 159 | as published (contains 232,578 exact duplicate points) |
| `test.laz` | 357,435 | 64 | as published; the plot used for published scores |
| `train_test.laz` | 1,459,112 | 222 | train + test merged by `processes/merge_labelled.py`, duplicates removed |

Train and test are two parts of one plot with one tree numbering. They overlap at the border
(tree 2 is in both), so the merged file has 222 trees, not 223.
`tree_data_RMIT.csv` gives the field DBH of some trees.

The scripts read `.laz` directly. To get `.las` back: `laspy` (`laspy.read("x.laz").write("x.las")`) or CloudCompare.
