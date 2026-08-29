#Imports von stats.py

import datetime
import io
import json
import statistics
from collections import defaultdict
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import pickle
import os
import sys

sys.path.append(os.path.join('C:/','Users','David Heik','Desktop','Arbeit2024','PySCFabSim','Projekt-Reproduktion','Mai-Session', 'PySCFabSim-release','simulation'))

from classes import Lot, Step


import pickle
import statistics
import argparse
import os


def load_debug_data(path):
    print(path)
    with open(path, 'rb') as f:
        return pickle.load(f)


def analyze(debug_data):

    lots = defaultdict(lambda: {'ACT': [], 'throughput_one_year': 0 , 'throughput': 0, 'on_time': 0, 'tardiness': 0,'early_tardiness':0, 'waiting_time': 0,
                                'processing_time': 0, 'transport_time': 0, 'waiting_time_batching': 0, 'late': 0, 'on_time_one_year':0, 'late_one_year':0, })




    
    
    done_lots = debug_data['instance_done_lots']
    cqt_violated = debug_data['instance_counter_cqt_violated']
    instance_machines = debug_data['instance_machines']
    print("instance_machines", instance_machines)
    apt = debug_data['apt']
    dl = debug_data['dl']


    for lot in done_lots:
        if lot.release_at >= 0:
            lots[lot.name]['throughput'] += 1
            if lot.done_at <= lot.deadline_at:
                lots[lot.name]['on_time'] += 1
            else: 
                lots[lot.name]['late'] += 1  

            if lot.release_at >= 31536000:   
                lots[lot.name]['throughput_one_year'] += 1
                if lot.done_at <= lot.deadline_at:
                    lots[lot.name]['on_time_one_year'] += 1
                else: 
                    lots[lot.name]['late_one_year'] += 1  
                
                lots[lot.name]['ACT'].append(lot.done_at - lot.release_at)
                lots[lot.name]['tardiness'] += max(0, lot.done_at - lot.deadline_at)
                lots[lot.name]['early_tardiness'] += max(0, lot.deadline_at - lot.done_at)
                lots[lot.name]['waiting_time'] += lot.waiting_time
                lots[lot.name]['waiting_time_batching'] += lot.waiting_time_batching
                lots[lot.name]['processing_time'] += lot.processing_time
                lots[lot.name]['transport_time'] += lot.transport_time

                if lot.name not in apt:
                    apt[lot.name] = sum([s.processing_time.avg() for s in lot.processed_steps])
                    dl[lot.name] = lot.deadline_at - lot.release_at


    #print("ONE_YEAR")
    #print('Lot', 'TH', 'ACT', 'ONTIME', 'LATE' , 'throughput_one_year')
    acts = []
    ths = []
    ontimes = []
    for lot_name in sorted(list(lots.keys())):
        l = lots[lot_name]
        avg = statistics.mean(l['ACT']) / 3600 / 24
        lots[lot_name]['ACT'] = avg
        acts += [avg]
        th = lots[lot_name]['throughput']
        late = lots[lot_name]['late_one_year']
        ths += [th]
        ontime = round(l['on_time_one_year'] / l['throughput_one_year'] * 100,2)
        ontimes += [ontime]        
        #print(lot_name, th, round(avg, 1), ontime , late , l['throughput_one_year'])

    #print('---------------')
    #print(round(statistics.mean(acts), 2), statistics.mean(ths), statistics.mean(ontimes))
    #print(round(sum(acts), 2), sum(ths), sum(ontimes))
    #print('---------------')

    #print("All_YEARs")
    #print('Lot', 'TH', 'ACT', 'ONTIME', 'LATE')
    acts = []
    ths = []
    ontimes = []
    for lot_name in sorted(list(lots.keys())):
        l = lots[lot_name]
        avg = l['ACT']
        acts += [avg]
        th = lots[lot_name]['throughput']
        late = lots[lot_name]['late']
        ths += [th]
        ontime = round(l['on_time'] / l['throughput'] * 100,2)
        ontimes += [ontime]        
        #print(lot_name, th, round(avg, 1), ontime , late)

    #print('---------------')
    #print(round(statistics.mean(acts), 2), statistics.mean(ths), statistics.mean(ontimes))
    #print(round(sum(acts), 2), sum(ths), sum(ontimes))
    #print('---------------')
    

    ######################################
    #### Machien
    if True== True:
        utilized_times = defaultdict(lambda: [])
        setup_times = defaultdict(lambda: [])
        pm_times = defaultdict(lambda: [])
        br_times = defaultdict(lambda: [])
        for machine in instance_machines:
            utilized_times[machine.family].append(machine.utilized_time)
            setup_times[machine.family].append(machine.setuped_time)
            pm_times[machine.family].append(machine.pmed_time)
            br_times[machine.family].append(machine.bred_time)

        print('Machine', 'Cnt', 'avail','util', 'br', 'pm', 'setup')
        machines = defaultdict(lambda: {})
        for machine_name in sorted(list(utilized_times.keys())):
            time = 31536000 #instance_machines.current_time - 31536000 #if not wip else instance.current_time
            av = (time - statistics.mean(pm_times[machine_name]) - statistics.mean(br_times[machine_name]))
            machines[machine_name]['avail'] = av / time
            machines[machine_name]['util'] = statistics.mean(utilized_times[machine_name]) / av
            machines[machine_name]['pm'] = statistics.mean(pm_times[machine_name]) / time
            machines[machine_name]['br'] = statistics.mean(br_times[machine_name]) / time
            machines[machine_name]['setup'] = statistics.mean(setup_times[machine_name]) / time

            print(machine_name, len(utilized_times[machine_name]),
                round(machines[machine_name]['avail'] * 100, 2),
                round(machines[machine_name]['util'] * 100, 2),
                round(machines[machine_name]['br'] * 100, 2),
                round(machines[machine_name]['pm'] * 100, 2),
                round(machines[machine_name]['setup'] * 100, 2))






if __name__ == "__main__":

    file = "greedy/debug_data_greedy_seed118_730days_SMT2020_HVLM_fifo_seed-118.pkl"
    file = "greedy/debug_data_greedy_seed118_730days_SMT2020_HVLM_fifo_seed-118.pkl"
    file = "greedy/debug_data_greedy_seed118_730days_SMT2020_HVLM_fifo_seed-118.pkl"
    file = "greedy/debug_data_greedy_seed118_730days_SMT2020_HVLM_fifo_seed-118.pkl"
    file = "greedy/debug_data_greedy_seed3298_730days_SMT2020_HVLM_fifo_seed-3298.pkl"
    
    file = "greedy/debug_data_greedy_seed670_730days_SMT2020_HVLM_fifo_seed-670.pkl"
    #file = "greedy/debug_data_greedy_seed118_730days_SMT2020_HVLM_fifo_seed-118.pkl"
    #file = "greedy/debug_data_greedy_seed651_730days_SMT2020_HVLM_fifo_seed-651.pkl"
    

    if not os.path.exists(file):
        print("Datei nicht gefunden:", file)
    else:
        data = load_debug_data(file)
        analyze(data)
