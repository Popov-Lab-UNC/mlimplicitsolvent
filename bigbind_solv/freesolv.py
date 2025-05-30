### We have saved the freesolv database locally

import os
import subprocess
import pandas as pd
from openmm import unit
from sim import SolvationSim
import time
#from configs import default
import sys

def load_freesolv():
    #return pd.read_csv("data/freesolv.txt", delimiter=";", comment="#", names=["compound", "smiles", "compound_name", "exp_dG", "exp_uncertainty", "calc_dG", "calc_uncertainty", "exp_ref", "cacl_ref", "notes"])
    return pd.read_csv(
        '/work/users/r/d/rdey/ml_implicit_solvent/freesolv/SAMPL.csv')


def smi_to_protonated_sdf(smiles, output_path):
    cmd = f'obabel "-:{smiles}" -O "{output_path}" --gen3d --pH 7'
    subprocess.run(cmd, check=True, shell=True, timeout=60)


equil_steps = 25000


def analyze_freesolv():

    df = load_freesolv()
    with open("output/freesolv_results.txt", "w") as f:
        for i, row in df.iterrows():
            out_folder = os.path.join(CONFIG.cache_dir,
                                      f"freesolv_{equil_steps}_better",
                                      row.compound)
            os.makedirs(out_folder, exist_ok=True)

            print(f"Processing {row.compound_name}")
            print(f"Saving results to {out_folder}")

            lig_file = os.path.join(out_folder, "ligand.sdf")
            if not os.path.exists(lig_file):
                smi_to_protonated_sdf(row.smiles, lig_file)

            sim = SolvationSim(lig_file, out_folder)
            sim.equil_steps = equil_steps
            sim.run_all()
            delta_F = sim.compute_delta_F()

            print(
                f"Computed delta_F: {delta_F.value_in_unit(unit.kilocalories_per_mole)} kcal/mol"
            )
            print(row)

            f.write(
                f"{row.compound_name},{delta_F.value_in_unit(unit.kilocalories_per_mole)},{row.exp_dG},{row.calc_dG}\n"
            )
            f.flush()


if __name__ == "__main__":
    init = time.time()
    master_path = "/work/users/r/d/rdey/GBn2_Test"
    smile = str(sys.argv[1])
    expt = float(sys.argv[2])
    name = str(sys.argv[3])
    
    folder_path = os.path.join(master_path, name)
    lig_file = os.path.join(folder_path, 'ligand.sdf')

    os.makedirs(folder_path, exist_ok=True)
    if(not os.path.exists(lig_file)):
        smi_to_protonated_sdf(smile, lig_file)
    sim = SolvationSim(lig_file, folder_path)
    sim.equil_steps = 15000
    sim.run_all()
    delta_F, Delta_dF = sim.compute_delta_F()
    print(f"Error: {Delta_dF}")
    print(f"Total Time: {time.time() - init}")
    print(f"{name}, {delta_F}, {expt}")

