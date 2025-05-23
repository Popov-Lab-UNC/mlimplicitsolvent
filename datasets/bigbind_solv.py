from datasets.md_batch import MDData
import torch
from torch.utils.data import Dataset
import h5py
import numpy as np
import os
from openmm.app.internal.customgbforces import GBSAGBn2Force
from config import CONFIG
from torch_geometric.data import Data
import numpy as np
from scipy import stats


class MAFBigBind(Dataset):

    def __init__(self, split, dir=CONFIG.bigbind_solv_dir):
        file_path = os.path.join(dir, split + ".h5")
        self.file = h5py.File(file_path, "r")
        self.keys = list(self.file.keys())
        self.length = len(self.keys)
        self.trim = 0.0

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        if index >= len(self):
            raise IndexError("Index out of bounds")

        group = self.file[self.keys[index]]
        q = group["charges"][:]
        positions = group["positions"][0]

        if positions.size == 0:
            raise IndexError("Empty positions encountered")

        # Remaining code as before
        atomic_numbers = group["atomic_numbers"][:]
        all_forces = group["solv_forces"][:]
        forces = np.mean(all_forces, axis=0)
        lambda_sterics = group["lambda_sterics"][0]
        lambda_electrostatics = group["lambda_electrostatics"][0]
        sterics_derivative = stats.trim_mean(
            np.array(group["sterics_derivatives"]), self.trim)
        electrostatics_derivative = stats.trim_mean(
            np.array(group["electrostatics_derivatives"]), self.trim)
        gbn2_params = group["gnn_params"][:]
        pre_params = np.concatenate([q[:, None], gbn2_params], axis=-1)
        force = GBSAGBn2Force(cutoff=None, SA="ACE", soluteDielectric=1.0)
        force.addParticles(pre_params)
        force.finalize()
        gnn_data = np.array([
            force.getParticleParameters(i)
            for i in range(force.getNumParticles())
        ])
        gbn_gnn_data = np.concatenate([
            gnn_data,
            torch.full((positions.shape[0], 1), lambda_electrostatics),
            torch.full((positions.shape[0], 1), lambda_sterics)
        ],
                                      axis=-1)

        return Data(charges=torch.tensor(q, dtype=torch.float32),
                    atom_features=torch.tensor(gbn_gnn_data,
                                               dtype=torch.float32),
                    pos=torch.tensor(positions, dtype=torch.float32),
                    atomic_numbers=torch.tensor(atomic_numbers,
                                                dtype=torch.long),
                    forces=torch.tensor(forces, dtype=torch.float32),
                    lambda_sterics=torch.tensor(lambda_sterics,
                                                dtype=torch.float32),
                    lambda_electrostatics=torch.tensor(lambda_electrostatics,
                                                       dtype=torch.float32),
                    sterics_derivative=torch.tensor(sterics_derivative,
                                                    dtype=torch.float32),
                    electrostatics_derivative=torch.tensor(
                        electrostatics_derivative, dtype=torch.float32))


class BigBindSolvDataset(Dataset):
    """ This dataset returns the charges, positions, atomic numbers,
    and forces of a frame in the bigbind_solv dataset."""

    def __init__(self, split, frame_index):
        """ Split is either 'train', 'val', or 'test'."""
        file_path = os.path.join(CONFIG.bigbind_solv_dir, split + ".h5")
        self.file = h5py.File(file_path, "r")
        self.keys = list(self.file.keys())
        self.length = len(self.keys)
        self.frame_index = frame_index

    def __len__(self):
        return self.length * self.frame_index

    def __getitem__(self, index):
        index_mod = index % self.length
        group = self.file[self.keys[index_mod]]

        q = group["charges"][:]
        all_positions = group["positions"][:]
        atomic_numbers = group["atomic_numbers"][:]
        all_forces = group["solv_forces"][:]

        # choose a random frame from the simulation
        if self.frame_index is None or (all_positions.shape[0]
                                        < self.frame_index):
            frame_idx = torch.randint(0, all_positions.shape[0], (1, )).item()
        else:
            frame_idx = (index % self.frame_index)

        positions = all_positions[frame_idx]
        forces = all_forces[frame_idx]
        lambda_sterics = group["lambda_sterics"][frame_idx]
        lambda_electrostatics = group["lambda_electrostatics"][frame_idx]
        sterics_derivative = group["sterics_derivatives"][frame_idx]
        electrostatics_derivative = group["electrostatics_derivatives"][
            frame_idx]

        charges = torch.tensor(q, dtype=torch.float32)
        positions = torch.tensor(positions, dtype=torch.float32)
        atomic_numbers = torch.tensor(atomic_numbers, dtype=torch.long)
        forces = torch.tensor(forces, dtype=torch.float32)
        lambda_sterics = torch.tensor(lambda_sterics, dtype=torch.float32)
        lambda_electrostatics = torch.tensor(lambda_electrostatics,
                                             dtype=torch.float32)
        sterics_derivative = torch.tensor(sterics_derivative,
                                          dtype=torch.float32)
        electrostatics_derivative = torch.tensor(electrostatics_derivative,
                                                 dtype=torch.float32)

        return MDData(
            charges=charges,
            positions=positions,
            atomic_numbers=atomic_numbers,
            forces=forces,
            lambda_sterics=lambda_sterics,
            lambda_electrostatics=lambda_electrostatics,
            sterics_derivative=sterics_derivative,
            electrostatics_derivative=electrostatics_derivative,
        )
