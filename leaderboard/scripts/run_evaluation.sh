#!/bin/bash
#export CARLA_ROOT=/home/mohammad/Mohammad_ws/autonomous_driving/e2etransfuser/carla
export CARLA_ROOT=/localhome/pagand/projects/e2etransfuser/carla/
export CARLA_SERVER=${CARLA_ROOT}/CarlaUE4.sh
export PYTHONPATH=$PYTHONPATH:${CARLA_ROOT}/PythonAPI
export PYTHONPATH=$PYTHONPATH:${CARLA_ROOT}/PythonAPI/carla
export PYTHONPATH=$PYTHONPATH:$CARLA_ROOT/PythonAPI/carla/dist/carla-0.9.10-py3.7-linux-x86_64.egg
export PYTHONPATH=$PYTHONPATH:leaderboard
export PYTHONPATH=$PYTHONPATH:leaderboard/team_code
export PYTHONPATH=$PYTHONPATH:scenario_runner

export LEADERBOARD_ROOT=leaderboard
export CHALLENGE_TRACK_CODENAME=SENSORS
export DEBUG_CHALLENGE=0
export REPETITIONS=1 
export RESUME=True

export WEATHER=ClearNoon # ClearNoon, ClearSunset, CloudyNoon, CloudySunset, WetNoon, WetSunset, MidRainyNoon, MidRainSunset, WetCloudyNoon, WetCloudySunset, HardRainNoon, HardRainSunset, SoftRainNoon, SoftRainSunset
export MODEL=x13 #transfuser geometric_fusion late_fusion aim cilrs s13 x13
export CONTROL_OPTION=one_of #one_of both_must pid_only mlp_only, control option is only for s13 and x13 
export SAVE_PATH=data0/x13_NORMAL/${WEATHER}/${MODEL}-${CONTROL_OPTION} # ADVERSARIAL NORMAL
export ROUTES=leaderboard/data/all_routes/routes_town05_short.xml #look at leaderboard/data/all_routes
#export SCENARIOS=leaderboard/data/scenarios/town05_all_scenarios.json #look at leaderboard/data/scenarios town05_all_scenarios OR no_scenarios.json
export SCENARIOS=leaderboard/data/scenarios/no_scenarios.json
export PORT=2006 # same as the carla server port
export TM_PORT=2056 # port for traffic manager, required when spawning multiple servers/clients
#export TEAM_CONFIG=/home/mohammad/Mohammad_ws/autonomous_driving/e2etransfuser/log/testmin_cvt_v2/retrain
export TEAM_CONFIG=/home/mohammad/Desktop/models/main
export CHECKPOINT_ENDPOINT=${SAVE_PATH}/eval_result.json # results file
export TEAM_AGENT=leaderboard/team_code/${MODEL}_agent.py

python3 ${LEADERBOARD_ROOT}/leaderboard/leaderboard_evaluator.py \
--scenarios=${SCENARIOS}  \
--routes=${ROUTES} \
--repetitions=${REPETITIONS} \
--track=${CHALLENGE_TRACK_CODENAME} \
--checkpoint=${CHECKPOINT_ENDPOINT} \
--agent=${TEAM_AGENT} \
--agent-config=${TEAM_CONFIG} \
--debug=${DEBUG_CHALLENGE} \
--record=${RECORD_PATH} \
--resume=${RESUME} \
--port=${PORT} \
--trafficManagerPort=${TM_PORT} \
--weather=${WEATHER} #buat ganti2 weather
