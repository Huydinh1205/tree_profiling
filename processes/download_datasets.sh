#!/bin/bash
# Same as download_datasets.bat, for Mac/Linux. Run from the processes folder; re-run to resume.
cd "$(dirname "$0")"; D=../data
mkdir -p $D/FOR-instanceV2 $D/SyntheticForest $D/FOR-instance $D/CedarCypress3D
curl -L -C - -o $D/FOR-instanceV2/test_data.zip      "https://zenodo.org/api/records/16742708/files/test_data.zip/content"
curl -L -C - -o $D/FOR-instanceV2/train_val_data.zip "https://zenodo.org/api/records/16742708/files/train_val_data.zip/content"
curl -L -C - -o $D/SyntheticForest/WoodLand.pcd      "https://zenodo.org/api/records/17568131/files/WoodLand.pcd/content"
curl -L -C - -o $D/SyntheticForest/OpenForest.pcd    "https://zenodo.org/api/records/17568131/files/OpenForest.pcd/content"
curl -L -C - -o $D/SyntheticForest/ClosedForest.pcd  "https://zenodo.org/api/records/17568131/files/ClosedForest.pcd/content"
curl -L -C - -o $D/FOR-instance/FORinstance_dataset.zip "https://zenodo.org/api/records/8287792/files/FORinstance_dataset.zip/content"
curl -L -C - -o $D/CedarCypress3D/CedarCypress3D_data.zip "https://zenodo.org/api/records/22168721/files/CedarCypress3D_data.zip/content"
