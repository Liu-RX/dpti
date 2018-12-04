#!/usr/bin/env python3

import numpy as np

def get_natoms(filename) :
    with open(filename, 'r') as fp :
        lines = fp.read().split('\n')
    for ii in lines :
        if 'atoms' in ii:
            natoms = int(ii.split()[0])
            return natoms
    raise RuntimeError("cannot find key word \'atoms\' in "+conf)

def get_thermo(filename) :
    with open(filename, 'r') as fp :
        fc = fp.read().split('\n')
    for sl in range(len(fc)) :
        if 'Step KinEng PotEng TotEng' in fc[sl] :
            break
    for el in range(len(fc)) :
        if 'Loop time of' in fc[el] :
            break
    data = []
    for ii in range(sl+1, el) :
        data.append([float(jj) for jj in fc[ii].split()])
    data = np.array(data)
    return data

def get_last_dump(dump) :
    with open(dump, 'r') as fp :
        lines = fp.read().split('\n')
    step_idx = -1
    for idx,ii in enumerate(lines) :
        if 'ITEM: TIMESTEP' in ii :
            step_idx = idx
    if step_idx == -1 :
        raise RuntimeError("cannot find timestep in lammps dump, something wrong")    
    return '\n'.join(lines[step_idx:])