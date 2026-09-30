# Unit conversion functions for KrishnaFlow AI

def feet_to_meters(ft):
    return ft * 0.3048

def cusecs_to_cumecs(cusecs):
    return cusecs / 35.3146667

def tmc_to_mcm(tmc):
    return tmc * 28.3168466

def delta_tmc_per_day(inflow_cusecs, outflow_cusecs):
    return (inflow_cusecs - outflow_cusecs) * 0.0000864
