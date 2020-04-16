#!/usr/bin/env python3

import os, sys, json, argparse, glob, shutil
import numpy as np
import scipy.constants as pc
import pymbar

import einstein
from lib.utils import create_path
from lib.utils import copy_file_list
from lib.utils import block_avg
from lib.utils import integrate_range
# from lib.utils import integrate_sys_err
from lib.utils import compute_nrefine
from lib.utils import parse_seq
from lib.utils import get_task_file_abspath
from lib.lammps import get_thermo
from lib.lammps import get_natoms

def make_iter_name (iter_index) :
    return "task_hti." + ('%04d' % iter_index)


def _ff_lj_on(lamb,
              model,
              sparam):
    nn = sparam['n']
    alpha_lj = sparam['alpha_lj']
    rcut = sparam['rcut']
    epsilon = sparam['epsilon']
    sigma_oo = sparam['sigma_oo']
    sigma_oh = sparam['sigma_oh']
    sigma_hh = sparam['sigma_hh']
    activation = sparam['activation']
    ret = ''
    ret += 'variable        EPSILON equal %f\n' % epsilon
    ret += 'pair_style      lj/cut/soft %f %f %f  \n' % (nn, alpha_lj, rcut)
    ret += 'pair_coeff      1 1 ${EPSILON} %f %f\n' % (sigma_oo, activation)
    ret += 'pair_coeff      1 2 ${EPSILON} %f %f\n' % (sigma_oh, activation)
    ret += 'pair_coeff      2 2 ${EPSILON} %f %f\n' % (sigma_hh, activation)
    ret += 'fix             tot_pot all adapt/fep 0 pair lj/cut/soft epsilon * * v_LAMBDA scale yes\n'
    ret += 'compute         e_diff all fep ${TEMP} pair lj/cut/soft epsilon * * v_EPSILON\n'    
    return ret


def _ff_deep_on(lamb,
                model,
                sparam):
    nn = sparam['n']
    alpha_lj = sparam['alpha_lj']
    rcut = sparam['rcut']
    epsilon = sparam['epsilon']
    sigma_oo = sparam['sigma_oo']
    sigma_oh = sparam['sigma_oh']
    sigma_hh = sparam['sigma_hh']
    activation = sparam['activation']
    ret = ''
    ret += 'variable        EPSILON equal %f\n' % epsilon
    ret += 'variable        ONE equal 1\n'
    ret += 'pair_style      hybrid/overlay deepmd %s lj/cut/soft %f %f %f  \n' % (model, nn, alpha_lj, rcut)
    ret += 'pair_coeff      * * deepmd\n'
    ret += 'pair_coeff      1 1 lj/cut/soft ${EPSILON} %f %f\n' % (sigma_oo, activation)
    ret += 'pair_coeff      1 2 lj/cut/soft ${EPSILON} %f %f\n' % (sigma_oh, activation)
    ret += 'pair_coeff      2 2 lj/cut/soft ${EPSILON} %f %f\n' % (sigma_hh, activation)
    ret += 'fix             tot_pot all adapt/fep 0 pair deepmd scale * * v_LAMBDA\n'
    ret += 'compute         e_diff all fep ${TEMP} pair deepmd scale * * v_ONE\n'
    return ret


def _ff_lj_off(lamb,
               model, 
               sparam) :
    nn = sparam['n']
    alpha_lj = sparam['alpha_lj']
    rcut = sparam['rcut']
    epsilon = sparam['epsilon']
    sigma_oo = sparam['sigma_oo']
    sigma_oh = sparam['sigma_oh']
    sigma_hh = sparam['sigma_hh']
    activation = sparam['activation']
    ret = ''
    ret += 'variable        EPSILON equal %f\n' % epsilon
    ret += 'variable        INV_EPSILON equal -${EPSILON}\n'
    ret += 'pair_style      hybrid/overlay deepmd %s lj/cut/soft %f %f %f  \n' % (model, nn, alpha_lj, rcut)
    ret += 'pair_coeff      * * deepmd\n'
    ret += 'pair_coeff      1 1 lj/cut/soft ${EPSILON} %f %f\n' % (sigma_oo, activation)
    ret += 'pair_coeff      1 2 lj/cut/soft ${EPSILON} %f %f\n' % (sigma_oh, activation)
    ret += 'pair_coeff      2 2 lj/cut/soft ${EPSILON} %f %f\n' % (sigma_hh, activation)
    ret += 'fix             tot_pot all adapt/fep 0 pair lj/cut/soft epsilon * * v_INV_LAMBDA scale yes\n'
    ret += 'compute         e_diff all fep ${TEMP} pair lj/cut/soft epsilon * * v_INV_EPSILON\n'    
    return ret
    

def _ff_spring(lamb,
               spring_k,
               var_spring):
    ret = ''
    if type(spring_k) is not list :
        if var_spring :
            spring_const = spring_k * (1 - lamb)
        else:
            spring_const = spring_k            
        ret += 'fix             l_spring all spring/self %.10e\n' % (spring_const)
        ret += 'fix_modify      l_spring energy yes\n'
    else :
        ntypes = len(spring_k)
        for ii in range(ntypes) :
            ret += 'group           type_%s type %s\n' % (ii+1, ii+1)
        for ii in range(ntypes) :
            if var_spring:
                spring_const = spring_k[ii] * (1 - lamb)
            else:
                spring_const = spring_k[ii]
            ret += 'fix             l_spring_%s type_%s spring/self %.10e\n' % (ii+1, ii+1, spring_const)
            ret += 'fix_modify      l_spring_%s energy yes\n' % (ii+1)
        sum_str = 'f_l_spring_1'
        for ii in range(1,ntypes) :
            sum_str += '+f_l_spring_%s' % (ii+1)
        ret += 'variable        l_spring equal %s\n' % (sum_str)
    return ret

    
def _ff_soft_lj(lamb,
                model,
                spring_k,
                step,
                sparam):
    ret = ''
    ret += '# --------------------- FORCE FIELDS ---------------------\n'
    if step == 'lj_on':
        ret += _ff_lj_on(lamb, model, sparam)
        var_spring = False
    elif step == 'deep_on':
        ret += _ff_deep_on(lamb, model, sparam)
        var_spring = False
    elif step == 'spring_off':
        ret += _ff_lj_off(lamb, model, sparam)
        var_spring = True
    else:
        raise RuntimeError('unkown step', step)

    ret += _ff_spring(lamb, spring_k, var_spring)
    
    return ret


def _ff_two_steps(lamb,
                  model,
                  spring_k,
                  step):
    ret = ''
    ret += '# --------------------- FORCE FIELDS ---------------------\n'
    ret += 'pair_style      deepmd %s\n' % model
    ret += 'pair_coeff\n'
    
    if step == 'both' or step == 'spring_off':                
        var_spring = True
    elif step == 'deep_on':
        var_spring = False
    else:
        raise RuntimeError('unkown step', step)
    if step == 'both' or step == 'deep_on':                
        var_deep = True
    elif step == 'spring_off':
        var_deep = False
    else:
        raise RuntimeError('unkown step', step)

    ret += _ff_spring(lamb, spring_k, var_spring)

    if var_deep:
        ret += 'fix             l_deep all adapt 1 pair deepmd scale * * v_LAMBDA\n'
    ret += 'compute         e_deep all pe pair\n'
    return ret

    
def _gen_lammps_input (conf_file, 
                       mass_map,
                       lamb,
                       model,
                       spring_k,
                       nsteps,
                       dt,
                       ens,
                       temp,
                       pres = 1.0, 
                       tau_t = 0.1,
                       tau_p = 0.5,
                       prt_freq = 100, 
                       copies = None,
                       crystal = 'vega', 
                       sparam = {},
                       switch = 'one-step',
                       step = 'both') :
    if crystal == 'frenkel' :
        assert(type(spring_k) == list)
    ret = ''
    ret += 'clear\n'
    ret += '# --------------------- VARIABLES-------------------------\n'
    ret += 'variable        NSTEPS          equal %d\n' % nsteps
    ret += 'variable        THERMO_FREQ     equal %d\n' % prt_freq
    ret += 'variable        DUMP_FREQ       equal %d\n' % prt_freq
    ret += 'variable        TEMP            equal %f\n' % temp
    ret += 'variable        PRES            equal %f\n' % pres
    ret += 'variable        TAU_T           equal %f\n' % tau_t
    ret += 'variable        TAU_P           equal %f\n' % tau_p
    ret += 'variable        LAMBDA          equal %.10e\n' % lamb
    ret += 'variable        INV_LAMBDA      equal %.10e\n' % (1-lamb)
    ret += '# ---------------------- INITIALIZAITION ------------------\n'
    ret += 'units           metal\n'
    ret += 'boundary        p p p\n'
    ret += 'atom_style      atomic\n'
    ret += '# --------------------- ATOM DEFINITION ------------------\n'
    ret += 'box             tilt large\n'
    ret += 'read_data       %s\n' % conf_file
    if copies is not None :
        ret += 'replicate       %d %d %d\n' % (copies[0], copies[1], copies[2])
    ret += 'change_box      all triclinic\n'
    for jj in range(len(mass_map)) :
        ret += "mass            %d %f\n" %(jj+1, mass_map[jj])

    # force field setting
    if switch == 'one-step' or switch == 'two-step':
        ret += _ff_two_steps(lamb, model, spring_k, step)
    elif switch == 'three-step':
        ret += _ff_soft_lj(lamb, model, spring_k, step, sparam)
    else:
        raise RuntimeError('unknow switch', switch)

    ret += '# --------------------- MD SETTINGS ----------------------\n'    
    ret += 'neighbor        1.0 bin\n'
    ret += 'timestep        %s\n' % dt
    ret += 'thermo          ${THERMO_FREQ}\n'
    if 1 - lamb != 0 :
        if type(spring_k) is not list :        
            if switch == 'three-step':
                ret += 'thermo_style    custom step ke pe etotal enthalpy temp press vol f_l_spring c_e_diff[1]\n'
            else:
                ret += 'thermo_style    custom step ke pe etotal enthalpy temp press vol f_l_spring c_e_deep\n'
        else :
            if switch == 'three-step':
                ret += 'thermo_style    custom step ke pe etotal enthalpy temp press vol v_l_spring c_e_diff[1]\n'
            else:
                ret += 'thermo_style    custom step ke pe etotal enthalpy temp press vol v_l_spring c_e_deep\n'
    else :
        ret += 'thermo_style    custom step ke pe etotal enthalpy temp press vol c_e_deep c_e_deep\n'
    ret += 'thermo_modify   format 9 %.16e\n'
    ret += 'thermo_modify   format 10 %.16e\n'
    ret += '# dump            1 all custom ${DUMP_FREQ} dump.hti id type x y z vx vy vz\n'
    if ens == 'nvt' :
        ret += 'fix             1 all nvt temp ${TEMP} ${TEMP} ${TAU_T}\n'
    elif ens == 'nvt-langevin' :
        ret += 'fix             1 all nve\n'
        ret += 'fix             2 all langevin ${TEMP} ${TEMP} ${TAU_T} %d' % (np.random.randint(0, 2**16))
        if crystal == 'frenkel':
            ret += ' zero yes\n'
        else:
            ret += ' zero no\n'            
    elif ens == 'npt-iso' or ens == 'npt':
        ret += 'fix             1 all npt temp ${TEMP} ${TEMP} ${TAU_T} iso ${PRES} ${PRES} ${TAU_P}\n'
    elif ens == 'nve' :
        ret += 'fix             1 all nve\n'
    else :
        raise RuntimeError('unknow ensemble %s\n' % ens)        
    ret += '# --------------------- INITIALIZE -----------------------\n'    
    ret += 'velocity        all create ${TEMP} %d\n' % (np.random.randint(0, 2**16))
    if crystal == 'frenkel' :
        ret += 'fix             fc all recenter INIT INIT INIT\n'
        ret += 'fix             fm all momentum 1 linear 1 1 1\n'
        ret += 'velocity        all zero linear\n'
    elif crystal == 'vega' :
        ret += 'group           first id 1\n'
        ret += 'fix             fc first recenter INIT INIT INIT\n'
        ret += 'fix             fm first momentum 1 linear 1 1 1\n'
        ret += 'velocity        first zero linear\n'
    else :
        raise RuntimeError('unknow crystal ' + crystal)
    ret += '# --------------------- RUN ------------------------------\n'    
    ret += 'run             ${NSTEPS}\n'
    ret += 'write_data      out.lmp\n'
    
    return ret

def _gen_lammps_input_ideal (conf_file, 
                             mass_map,
                             lamb,
                             model,
                             nsteps,
                             dt,
                             ens,
                             temp,
                             pres = 1.0, 
                             tau_t = 0.1,
                             tau_p = 0.5,
                             prt_freq = 100, 
                             copies = None,
                             norm_style = 'first') :
    ret = ''
    ret += 'clear\n'
    ret += '# --------------------- VARIABLES-------------------------\n'
    ret += 'variable        NSTEPS          equal %d\n' % nsteps
    ret += 'variable        THERMO_FREQ     equal %d\n' % prt_freq
    ret += 'variable        DUMP_FREQ       equal %d\n' % prt_freq
    ret += 'variable        TEMP            equal %f\n' % temp
    ret += 'variable        PRES            equal %f\n' % pres
    ret += 'variable        TAU_T           equal %f\n' % tau_t
    ret += 'variable        TAU_P           equal %f\n' % tau_p
    ret += 'variable        LAMBDA          equal %.10e\n' % lamb
    ret += 'variable        ZERO            equal 0\n'
    ret += '# ---------------------- INITIALIZAITION ------------------\n'
    ret += 'units           metal\n'
    ret += 'boundary        p p p\n'
    ret += 'atom_style      atomic\n'
    ret += '# --------------------- ATOM DEFINITION ------------------\n'
    ret += 'box             tilt large\n'
    ret += 'read_data       %s\n' % conf_file
    if copies is not None :
        ret += 'replicate       %d %d %d\n' % (copies[0], copies[1], copies[2])
    ret += 'change_box      all triclinic\n'
    for jj in range(len(mass_map)) :
        ret += "mass            %d %f\n" %(jj+1, mass_map[jj])
    ret += '# --------------------- FORCE FIELDS ---------------------\n'
    ret += 'pair_style      deepmd %s\n' % model
    ret += 'pair_coeff\n'
    ret += 'fix             l_deep all adapt 1 pair deepmd scale * * v_LAMBDA\n'
    ret += 'compute         e_deep all pe pair\n'
    ret += '# --------------------- MD SETTINGS ----------------------\n'    
    ret += 'neighbor        1.0 bin\n'
    ret += 'timestep        %s\n' % dt
    ret += 'thermo          ${THERMO_FREQ}\n'
    ret += 'thermo_style    custom step ke pe etotal enthalpy temp press vol v_ZERO c_e_deep\n'
    ret += 'thermo_modify   format 10 %.16e\n'
    ret += '# dump            1 all custom ${DUMP_FREQ} dump.hti id type x y z vx vy vz\n'
    if ens == 'nvt' :
        ret += 'fix             1 all nvt temp ${TEMP} ${TEMP} ${TAU_T}\n'
    elif ens == 'nvt-langevin' :
        ret += 'fix             1 all nve\n'
        ret += 'fix             2 all langevin ${TEMP} ${TEMP} ${TAU_T} %d zero yes\n' % (np.random.randint(0, 2**16))
    elif ens == 'npt-iso' or ens == 'npt':
        ret += 'fix             1 all npt temp ${TEMP} ${TEMP} ${TAU_T} iso ${PRES} ${PRES} ${TAU_P}\n'
    elif ens == 'nve' :
        ret += 'fix             1 all nve\n'
    else :
        raise RuntimeError('unknow ensemble %s\n' % ens)        
    ret += 'fix             mzero all momentum 10 linear 1 1 1\n'
    ret += '# --------------------- INITIALIZE -----------------------\n'    
    ret += 'velocity        all create ${TEMP} %d\n' % (np.random.randint(0, 2**16))
    ret += 'velocity        all zero linear\n'
    ret += '# --------------------- RUN ------------------------------\n'    
    ret += 'run             ${NSTEPS}\n'
    ret += 'write_data      out.lmp\n'
    
    return ret


def make_tasks(iter_name, jdata, ref, switch = 'one-step'):
    equi_conf = os.path.abspath(jdata['equi_conf'])
    model = os.path.abspath(jdata['model'])

    if switch == 'one-step':
        subtask_name = iter_name
        _make_tasks(subtask_name, jdata, ref, step = 'both')
    elif switch == 'two-step' or switch == 'three-step':
        create_path(iter_name)
        copied_conf = os.path.join(os.path.abspath(iter_name), 'conf.lmp')
        shutil.copyfile(equi_conf, copied_conf)
        jdata['equi_conf'] = 'conf.lmp'
        linked_model = os.path.join(os.path.abspath(iter_name), 'graph.pb')
        shutil.copyfile(model, linked_model)
        jdata['model'] = 'graph.pb'
        cwd = os.getcwd()
        os.chdir(iter_name)    
        with open('in.json', 'w') as fp:
            json.dump(jdata, fp, indent=4)
        if switch == 'two-step':
            subtask_name = '00.deep_on'
            _make_tasks(subtask_name, jdata, ref, switch = switch, step = 'deep_on', link = True)
            subtask_name = '01.spring_off'
            _make_tasks(subtask_name, jdata, ref, switch = switch, step = 'spring_off', link = True)
        elif switch == 'three-step':
            subtask_name = '00.lj_on'
            _make_tasks(subtask_name, jdata, ref, switch = switch, step = 'lj_on', link = True)
            subtask_name = '01.deep_on'
            _make_tasks(subtask_name, jdata, ref, switch = switch, step = 'deep_on', link = True)
            subtask_name = '02.spring_off'
            _make_tasks(subtask_name, jdata, ref, switch = switch, step = 'spring_off', link = True)
        else:
            raise RuntimeError('unknow switch', switch)
        os.chdir(cwd)
    else:
        raise RuntimeError('unknow switch', switch)

    
def _make_tasks(iter_name, jdata, ref, switch = 'one-step', step = 'both', link = False) :
    if 'crystal' not in jdata:
        print('do not find crystal in jdata, assume vega')
        jdata['crystal'] = 'vega'
    crystal = jdata['crystal']
    protect_eps = jdata['protect_eps']

    if switch == 'one-step':
        all_lambda = parse_seq(jdata['lambda'])
    elif switch == 'two-step' or switch == 'three-step':
        if step == 'deep_on':
            all_lambda = parse_seq(jdata['lambda_deep_on'])
        elif step == 'spring_off':
            all_lambda = parse_seq(jdata['lambda_spring_off'])
        elif step == 'lj_on':
            all_lambda = parse_seq(jdata['lambda_lj_on'])
        else:
            raise RuntimeError('unknown step', step)

    if all_lambda[0] == 0 :
        all_lambda[0] += protect_eps
    if all_lambda[-1] == 1 :
        all_lambda[-1] -= protect_eps
        
    equi_conf = jdata['equi_conf']
    equi_conf = os.path.abspath(equi_conf)
    model = jdata['model']
    model = os.path.abspath(model)
    model_mass_map = jdata['model_mass_map']
    nsteps = jdata['nsteps']
    dt = jdata['dt']
    spring_k = jdata['spring_k']
    sparam = jdata.get('soft_param', {})
    if crystal == 'frenkel' :
        spring_k_1 = []
        for ii in model_mass_map :
            spring_k_1.append(spring_k * ii)
        spring_k = spring_k_1
    stat_freq = jdata['stat_freq']
    copies = None
    if 'copies' in jdata :
        copies = jdata['copies']
    temp = jdata['temp']
    jdata['reference'] = ref
    jdata['switch'] = switch
    jdata['step'] = step

    create_path(iter_name)
    copied_conf = os.path.join(os.path.abspath(iter_name), 'conf.lmp')
    if not link :
        shutil.copyfile(equi_conf, copied_conf)
    else:
        cwd = os.getcwd()
        os.chdir(iter_name)
        os.symlink(os.path.relpath(equi_conf), 'conf.lmp')
        os.chdir(cwd)
    jdata['equi_conf'] = 'conf.lmp'    
    linked_model = os.path.join(os.path.abspath(iter_name), 'graph.pb')
    if not link:
        shutil.copyfile(model, linked_model)
    else:
        cwd = os.getcwd()
        os.chdir(iter_name)
        os.symlink(os.path.relpath(model), 'graph.pb')
        os.chdir(cwd)
    jdata['model'] = 'graph.pb'
    langevin = jdata.get('langevin', True)

    cwd = os.getcwd()
    os.chdir(iter_name)
    with open('in.json', 'w') as fp:
        json.dump(jdata, fp, indent=4)
    os.chdir(cwd)

    for idx,ii in enumerate(all_lambda) :
        work_path = os.path.join(iter_name, 'task.%06d' % idx)
        create_path(work_path)
        os.chdir(work_path)
        os.symlink(os.path.relpath(copied_conf), 'conf.lmp')
        os.symlink(os.path.relpath(linked_model), 'graph.pb')
        if idx == 0:
            ens = 'nvt-langevin'
        else :
            ens = 'nvt'
        if langevin:
            ens = 'nvt-langevin'        
        if ref == 'einstein' :
            lmp_str \
                = _gen_lammps_input('conf.lmp',
                                    model_mass_map, 
                                    ii, 
                                    'graph.pb',
                                    spring_k, 
                                    nsteps, 
                                    dt,
                                    ens,
                                    temp,
                                    prt_freq = stat_freq, 
                                    copies = copies,
                                    switch = switch,
                                    step = step,
                                    sparam = sparam,
                                    crystal = crystal)
        elif ref == 'ideal' :
            lmp_str \
                = _gen_lammps_input_ideal('conf.lmp',
                                          model_mass_map, 
                                          ii, 
                                          'graph.pb',
                                          nsteps, 
                                          dt,
                                          ens,
                                          temp,
                                          prt_freq = stat_freq, 
                                          copies = copies)
        else :
            raise RuntimeError('unknow reference system type ' + ref)
        with open('in.lammps', 'w') as fp :
            fp.write(lmp_str)
        with open('lambda.out', 'w') as fp :
            fp.write(str(ii))
        os.chdir(cwd)


def refine_task (from_task, to_task, err, print_ref=False) :
    from_task = os.path.abspath(from_task)
    to_task = os.path.abspath(to_task)
    
    from_ti = os.path.join(from_task, 'hti.out')
    if not os.path.isfile(from_ti) :
        raise RuntimeError("cannot find file %s, task should be computed befor refined" % from_ti)
    tmp_array = np.loadtxt(from_ti)
    all_t = tmp_array[:,0]
    integrand = tmp_array[:,1]
    ntask = all_t.size

    interval_nrefine = compute_nrefine(all_t, integrand, err)
    if print_ref:
        print(interval_nrefine)
        return

    refined_t = []
    back_map = []
    for ii in range(0, ntask-1) :
        refined_t.append(all_t[ii])
        back_map.append(ii)
        hh = (all_t[ii+1] - all_t[ii]) / interval_nrefine[ii]
        for jj in range(1, interval_nrefine[ii]) :
            refined_t.append(all_t[ii] + jj * hh)
            back_map.append(-1)
    refined_t.append(all_t[-1])
    back_map.append(ntask-1)

    from_json = os.path.join(from_task, 'in.json')
    to_json = os.path.join(to_task, 'in.json')
    from_jdata = json.load(open(from_json))
    to_jdata = from_jdata

    to_jdata['lambda'] = refined_t
    to_jdata['orig_task'] = from_task
    to_jdata['back_map'] = back_map
    to_jdata['refine_error'] = err
    to_jdata['equi_conf'] = get_task_file_abspath(from_task, from_jdata['equi_conf'])
    to_jdata['model'] = get_task_file_abspath(from_task, from_jdata['model'])

    make_tasks(to_task, to_jdata, to_jdata['reference'])

    from_task_list = glob.glob(os.path.join(from_task, 'task.[0-9]*'))
    from_task_list.sort()
    to_task_list = glob.glob(os.path.join(to_task, 'task.[0-9]*'))
    to_task_list.sort()
    assert(len(from_task_list) == ntask)
    assert(len(to_task_list) == len(refined_t))

    for ii in range(len(to_task_list)) :
        if back_map[ii] < 0 : 
            continue
        for jj in ['data', 'log.lammps'] :
            shutil.copyfile(
                os.path.join(from_task_list[back_map[ii]], jj), 
                os.path.join(to_task_list[ii], jj), 
            )
        with open(os.path.join(to_task_list[ii], 'from.dir'), 'w') as fp:
            fp.write(from_task_list[back_map[ii]])
    

def _compute_thermo(fname, natoms, stat_skip, stat_bsize) :
    data = get_thermo(fname)
    ea, ee = block_avg(data[:, 3], skip = stat_skip, block_size = stat_bsize)
    ha, he = block_avg(data[:, 4], skip = stat_skip, block_size = stat_bsize)
    ta, te = block_avg(data[:, 5], skip = stat_skip, block_size = stat_bsize)
    pa, pe = block_avg(data[:, 6], skip = stat_skip, block_size = stat_bsize)
    va, ve = block_avg(data[:, 7], skip = stat_skip, block_size = stat_bsize)
    thermo_info = {}
    thermo_info['p'] = pa
    thermo_info['p_err'] = pe
    thermo_info['v'] = va / natoms
    thermo_info['v_err'] = ve / np.sqrt(natoms)
    thermo_info['e'] = ea / natoms
    thermo_info['e_err'] = ee / np.sqrt(natoms)
    thermo_info['h'] = ha / natoms
    thermo_info['h_err'] = he / np.sqrt(natoms)
    thermo_info['t'] = ta
    thermo_info['t_err'] = te
    unit_cvt = 1e5 * (1e-10**3) / pc.electron_volt
    thermo_info['pv'] = pa * va * unit_cvt / natoms
    thermo_info['pv_err'] = pe * va * unit_cvt  / np.sqrt(natoms)
    return thermo_info


def post_tasks(iter_name, jdata, natoms = None, method = 'inte', scheme = 's'):
    switch = 'one-step'
    if os.path.isdir(os.path.join(iter_name, '00.deep_on')):
        switch = 'two-step'
    if os.path.isdir(os.path.join(iter_name, '00.lj_on')):
        switch = 'three-step'

    if switch == 'two-step':
        subtask_name = os.path.join(iter_name, '00.deep_on')
        if method == 'inte' :
            e0, err0, tinfo0 = _post_tasks(subtask_name, jdata, natoms = natoms, scheme = scheme, switch = switch, step = 'deep_on')
        elif method == 'mbar':
            e0, err0, tinfo0 = _post_tasks_mbar(subtask_name, jdata, natoms = natoms, switch = switch, step = 'deep_on')
        else :
            raise RuntimeError('unknow method for integration')
        print('# fe of deep_on:    %20.12f  %10.3e %10.3e' % (e0, err0[0], err0[1]))
        subtask_name = os.path.join(iter_name, '01.spring_off')
        if method == 'inte' :
            e1, err1, tinfo1 = _post_tasks(subtask_name, jdata, natoms = natoms, scheme = scheme, switch = switch, step = 'spring_off')
        elif method == 'mbar':
            e1, err1, tinfo1 = _post_tasks_mbar(subtask_name, jdata, natoms = natoms, switch = switch, step = 'spring_off')
        else :
            raise RuntimeError('unknow method for integration')
        print('# fe of spring_off: %20.12f  %10.3e %10.3e' % (e1, err1[0], err1[1]))
        de = e0 + e1
        stt_err = np.sqrt(np.square(err0[0]) + np.square(err1[0]))
        sys_err = ((err0[1]) + (err1[1]))
        err = [stt_err, sys_err]
        tinfo = tinfo1
    elif switch == 'three-step':
        subtask_name = os.path.join(iter_name, '00.lj_on')
        if method == 'inte' :
            e0, err0, tinfo0 = _post_tasks(subtask_name, jdata, natoms = natoms, scheme = scheme, switch = switch, step = 'lj_on')
        elif method == 'mbar':
            e0, err0, tinfo0 = _post_tasks_mbar(subtask_name, jdata, natoms = natoms, switch = switch, step = 'lj_on')
        else :
            raise RuntimeError('unknow method for integration')
        print('# fe of lj_on:      %20.12f  %10.3e %10.3e' % (e0, err0[0], err0[1]))
        subtask_name = os.path.join(iter_name, '01.deep_on')
        if method == 'inte' :
            e1, err1, tinfo1 = _post_tasks(subtask_name, jdata, natoms = natoms, scheme = scheme, switch = switch, step = 'deep_on')
        elif method == 'mbar':
            e1, err1, tinfo1 = _post_tasks_mbar(subtask_name, jdata, natoms = natoms, switch = switch, step = 'deep_on')
        else :
            raise RuntimeError('unknow method for integration')
        print('# fe of deep_off:   %20.12f  %10.3e %10.3e' % (e1, err1[0], err1[1]))
        subtask_name = os.path.join(iter_name, '02.spring_off')
        if method == 'inte' :
            e2, err2, tinfo2 = _post_tasks(subtask_name, jdata, natoms = natoms, scheme = scheme, switch = switch, step = 'spring_off')
        elif method == 'mbar':
            e2, err2, tinfo2 = _post_tasks_mbar(subtask_name, jdata, natoms = natoms, switch = switch, step = 'spring_off')
        else :
            raise RuntimeError('unknow method for integration')
        print('# fe of spring_off: %20.12f  %10.3e %10.3e' % (e2, err2[0], err2[1]))
        de = e0 + e1 + e2
        stt_err = np.sqrt(np.square(err0[0]) + np.square(err1[0]) + np.square(err2[0]))
        sys_err = ((err0[1]) + (err1[1]) + (err2[1]))
        err = [stt_err, sys_err]
        tinfo = tinfo2
    else:
        if method == 'inte':
            de, err, tinfo = _post_tasks(iter_name, jdata, natoms = natoms, scheme = scheme)
        elif method == 'mbar':
            de, err, tinfo = _post_tasks_mbar(iter_name, jdata, natoms = natoms)
    return de, err, tinfo
    

def _post_tasks(iter_name, jdata, natoms = None, scheme = 's', switch = 'one-step', step = 'both') :
    stat_skip = jdata['stat_skip']
    stat_bsize = jdata['stat_bsize']
    all_tasks = glob.glob(os.path.join(iter_name, 'task.[0-9]*'))
    all_tasks.sort()
    ntasks = len(all_tasks)
    equi_conf = get_task_file_abspath(iter_name, jdata['equi_conf'])
    assert(os.path.isfile(equi_conf))
    if natoms == None :
        natoms = get_natoms(equi_conf)
        if 'copies' in jdata :
            natoms *= np.prod(jdata['copies'])
    print('# natoms: %d' % natoms)
    
    all_lambda = []
    all_es = []
    all_es_err = []
    all_ed = []
    all_ed_err = []

    for ii in all_tasks :
        log_name = os.path.join(ii, 'log.lammps')
        data = get_thermo(log_name)
        np.savetxt(os.path.join(ii, 'data'), data, fmt = '%.6e')
        sa, se = block_avg(data[:, 8], skip = stat_skip, block_size = stat_bsize)
        da, de = block_avg(data[:, 9], skip = stat_skip, block_size = stat_bsize)
        sa /= natoms
        se /= np.sqrt(natoms)
        da /= natoms
        de /= np.sqrt(natoms)
        lmda_name = os.path.join(ii, 'lambda.out')
        ll = float(open(lmda_name).read())
        all_lambda.append(ll)
        all_es.append(sa)
        all_ed.append(da)
        all_es_err.append(se)
        all_ed_err.append(de)

    all_lambda = np.array(all_lambda)
    all_es = np.array(all_es)
    all_ed = np.array(all_ed)
    all_es_err = np.array(all_es_err)
    all_ed_err = np.array(all_ed_err)
    if switch == 'one-step' or switch == 'two-step':
        if step == 'both':
            de = all_ed / all_lambda - all_es / (1 - all_lambda)
            all_err = np.sqrt(np.square(all_ed_err / all_lambda) + np.square(all_es_err / (1 - all_lambda)))
        elif step == 'deep_on':
            de = all_ed / all_lambda
            all_err = all_ed_err / all_lambda
        elif step == 'spring_off':
            de = -all_es / (1 - all_lambda)
            all_err = all_es_err / (1 - all_lambda)
        else:
            raise RuntimeError('unknow step', step)
    elif switch == 'three-step' :
        if step == 'lj_on' or step == 'deep_on':
            de = all_ed
            all_err = all_ed_err
        elif step == 'spring_off':
            de = -all_es / (1 - all_lambda) + all_ed
            all_err = np.sqrt( np.square(all_es_err / (1 - all_lambda)) +
                               np.square(all_ed_err) )
        else:
            raise RuntimeError('unknow step', step)
    else:
        raise RuntimeError('unknow switch', switch)

    all_print = []
    # all_print.append(np.arange(len(all_lambda)))
    all_print.append(all_lambda)
    all_print.append(de)
    all_print.append(all_err)
    all_print.append(all_ed / all_lambda)
    all_print.append(all_es / (1 - all_lambda))
    all_print.append(all_ed_err / all_lambda)
    all_print.append(all_es_err / (1 - all_lambda))
    all_print = np.array(all_print)
    np.savetxt(os.path.join(iter_name, 'hti.out'), 
               all_print.T, 
               fmt = '%.8e', 
               header = 'lmbda dU dU_err Ud Us Ud_err Us_err')

    new_lambda, i, i_e, s_e = integrate_range(all_lambda, de, all_err, scheme = scheme)
    if new_lambda[-1] != all_lambda[-1] :
        if new_lambda[-1] == all_lambda[-2]:
            _, i1, i_e1, s_e1 = integrate_range(all_lambda[-2:], de[-2:], all_err[-2:], scheme='t')
            diff_e = i[-1] + i1[-1]
            err = np.linalg.norm([s_e[-1], s_e1[-1]])
            sys_err = i_e[-1] + i_e1[-1]
        else :
            raise RuntimeError("lambda does not match!")
    else:
        diff_e = i[-1]
        err = s_e[-1]
        sys_err = i_e[-1]
    
    # diff_e, err = integrate(all_lambda, de, all_err)
    # sys_err = integrate_sys_err(all_lambda, de)

    path_endpnt = os.path.join(iter_name, 'task.endpnt')
    if os.path.isdir(path_endpnt) :
        print('# Found end point, compute thermo info from it')
        thermo_info = _compute_thermo(os.path.join(path_endpnt, 'log.lammps'),
                                      natoms,
                                      stat_skip, stat_bsize)
    else :
        print('# Not found end point, compute thermo info from the last lambda')
        thermo_info = _compute_thermo(os.path.join(all_tasks[-1], 'log.lammps'),
                                      natoms,
                                      stat_skip, stat_bsize)

    return diff_e, [err,sys_err], thermo_info


def _post_tasks_mbar(iter_name, jdata, natoms = None, switch = 'one-step', step = 'both') :
    stat_skip = jdata['stat_skip']
    stat_bsize = jdata['stat_bsize']
    all_tasks = glob.glob(os.path.join(iter_name, 'task.[0-9]*'))
    all_tasks.sort()
    ntasks = len(all_tasks)
    equi_conf = jdata['equi_conf']
    cwd = os.getcwd()
    os.chdir(iter_name)
    assert(os.path.isfile(equi_conf))
    equi_conf = os.path.abspath(equi_conf)
    os.chdir(cwd)
    temp = jdata['temp']
    if natoms == None :
        natoms = get_natoms(equi_conf)
        if 'copies' in jdata :
            natoms *= np.prod(jdata['copies'])
    print('# natoms: %d' % natoms)
    
    all_lambda = []
    for ii in all_tasks :
        lmda_name = os.path.join(ii, 'lambda.out')
        ll = float(open(lmda_name).read())
        all_lambda.append(ll)
    all_lambda = np.array(all_lambda)
    nlambda = all_lambda.size

    ukn = np.array([])
    nk = []
    kt_in_ev = pc.Boltzmann * temp / pc.electron_volt
    for idx,ii in enumerate(all_tasks) :
        log_name = os.path.join(ii, 'log.lammps')
        data = get_thermo(log_name)
        np.savetxt(os.path.join(ii, 'data'), data, fmt = '%.6e')
        this_ed = data[:,9] / kt_in_ev
        this_es = data[:,8] / kt_in_ev
        this_ed = this_ed[stat_skip:]
        this_es = this_es[stat_skip:]
        nk.append(this_ed.size)
        if switch_style == 'both':
            ed = this_ed / all_lambda[idx]
            es = this_es / (1 - all_lambda[idx])
            block_u = []
            for ll in all_lambda :
                block_u.append(ed * ll + es * (1-ll))
        elif switch_style == 'deep_on':
            ed = this_ed / all_lambda[idx]
            block_u = []
            for ll in all_lambda :
                block_u.append(ed * ll)
        elif switch_style == 'spring_off':
            es = this_es / (1 - all_lambda[idx])
            block_u = []
            for ll in all_lambda :
                block_u.append(es * (1-ll))
        else:
            raise RuntimeError('unknown switch_style', switch_style)
        block_u = np.reshape(block_u, [nlambda, -1])
        if ukn.size == 0 :
            ukn = block_u 
        else :
            ukn = np.concatenate((ukn, block_u), axis = 1)
    nk = np.array(nk)

    mbar = pymbar.MBAR(ukn, nk)
    Deltaf_ij, dDeltaf_ij, Theta_ij = mbar.getFreeEnergyDifferences()
    Deltaf_ij = Deltaf_ij / natoms
    dDeltaf_ij = dDeltaf_ij / np.sqrt(natoms)

    diff_e = Deltaf_ij[0,-1] * kt_in_ev
    err = dDeltaf_ij[0,-1] * kt_in_ev

    thermo_info = _compute_thermo(os.path.join(all_tasks[-1], 'log.lammps'), 
                                  natoms,
                                  stat_skip, stat_bsize)

    return diff_e, [err,0], thermo_info


def print_thermo_info(info) :
    ptr = '# thermodynamics (normalized by natoms)\n'
    ptr += '# E (err)  [eV]:  %20.8f %20.8f\n' % (info['e'], info['e_err'])
    ptr += '# H (err)  [eV]:  %20.8f %20.8f\n' % (info['h'], info['h_err'])
    ptr += '# T (err)   [K]:  %20.8f %20.8f\n' % (info['t'], info['t_err'])
    ptr += '# P (err) [bar]:  %20.8f %20.8f\n' % (info['p'], info['p_err'])
    ptr += '# V (err) [A^3]:  %20.8f %20.8f\n' % (info['v'], info['v_err'])
    ptr += '# PV(err)  [eV]:  %20.8f %20.8f' % (info['pv'], info['pv_err'])
    print(ptr)

def _main ():
    parser = argparse.ArgumentParser(
        description="Compute free energy by Hamiltonian TI")
    subparsers = parser.add_subparsers(title='Valid subcommands', dest='command')

    parser_gen = subparsers.add_parser('gen', help='Generate a job')
    parser_gen.add_argument('PARAM', type=str ,
                            help='json parameter file')
    parser_gen.add_argument('-o','--output', type=str, default = 'new_job',
                            help='the output folder for the job')
    parser_gen.add_argument('-s','--switch', type=str, default = 'one-step',
                            choices = ['one-step', 'two-step', 'three-step'],
                            help='one-step: switching on DP and switching off spring simultanenously.\
                            two-step: 1 switching on DP, 2 switching off spring.\
                            three-step: 1 switching on soft LJ, 2 switching on DP, 3 switching off spring and soft LJ.')

    parser_comp = subparsers.add_parser('compute', help= 'Compute the result of a job')
    parser_comp.add_argument('JOB', type=str ,
                             help='folder of the job')
    parser_comp.add_argument('-t','--type', type=str, default = 'helmholtz', 
                             choices=['helmholtz', 'gibbs'], 
                             help='the type of free energy')
    parser_comp.add_argument('-m','--inte-method', type=str, default = 'inte', 
                             choices=['inte', 'mbar'], 
                             help='the method of thermodynamic integration')
    parser_comp.add_argument('-s','--scheme', type=str, default = 'simpson', 
                             help='the numeric integration scheme')
    args = parser.parse_args()

    if args.command is None :
        parser.print_help()
        exit
    if args.command == 'gen' :
        output = args.output
        jdata = json.load(open(args.PARAM, 'r'))
        if 'crystal' in jdata and jdata['crystal'] == 'frenkel' :
            print('# gen task with Frenkel\'s Einstein crystal')
        else :
            print('# gen task with Vega\'s Einstein molecule')
        make_tasks(output, jdata, 'einstein', args.switch)
    elif args.command == 'compute' :
        job = args.JOB
        jdata = json.load(open(os.path.join(job, 'in.json'), 'r'))
        if 'reference' not in jdata :
            jdata['reference'] = 'einstein'
        e0 = einstein.free_energy(job)
        de, de_err, thermo_info = post_tasks(job, jdata, method = args.inte_method, scheme = args.scheme)
        # printing
        print_format = '%20.12f  %10.3e  %10.3e'
        print_thermo_info(thermo_info)
        if jdata['reference'] == 'einstein' :
            print('# free ener of Einstein Mole: %20.8f' % e0)
        else :
            print('# free ener of ideal gas: %20.8f' % e0)            
        print(('# fe contrib due to integration ' + print_format) \
              % (de, de_err[0], de_err[1]))
        if args.type == 'helmholtz' :
            print('# Helmholtz free ener per atom (stat_err inte_err) [eV]:')
            print(print_format % (e0 + de, de_err[0], de_err[1]))
        if args.type == 'gibbs' :
            pv = thermo_info['pv']
            pv_err = thermo_info['pv_err']
            e1 = e0 + de + pv
            e1_err = np.sqrt(de_err[0]**2 + pv_err**2)
            print('# Gibbs free ener per atom (stat_err inte_err) [eV]:')
            print(print_format % (e1, e1_err, de_err[1]))
    
if __name__ == '__main__' :
    _main()
