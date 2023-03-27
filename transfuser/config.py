import os

class GlobalConfig:
    """ base architecture configurations """
	# Data
    seq_len = 1 # input timesteps
    pred_len = 4 # future waypoints predicted
    low_data = False

    root_dir = '/home/mohammad/Mohammad_ws/autonomous_driving/transfuser/data'  #14_weathers_full_data clear_noon_full_data
    train_towns = ['Town01' 'Town02', 'Town03', 'Town04', 'Town06', 'Town07', 'Town10']
    val_towns = ['Town05']
    # train_towns = ['Town00']
    # val_towns = ['Town00']
    train_data, val_data = [], []
    root_files = os.listdir(root_dir)
    
    for dir in root_files:
        scn_files = os.listdir(os.path.join(root_dir,dir))
        for routes in scn_files:
            for t in routes.split("_"):
                if t[0] != 'T':
                    continue
                if t in train_towns:
                    train_data.append(os.path.join(root_dir,dir, routes))
                    break
                elif t in val_towns:
                    val_data.append(os.path.join(root_dir,dir, routes))
                    break
                else:
                    break
    if low_data:
        train_data = train_data[:int(0.1*len(train_data))]
        val_data = val_data[:int(0.1*len(val_data))]


    # visualizing transformer attention maps
    # viz_root = '/mnt/qb/geiger/kchitta31/data_06_21'
    # viz_towns = ['Town05_tiny']
    # viz_data = []
    # for town in viz_towns:
    #     viz_data.append(os.path.join(viz_root, town))

    ignore_sides = True # don't consider side cameras
    ignore_rear = True # don't consider rear cameras
    n_views = 1 # no. of camera views

    input_resolution = 256

    scale = 1 # image pre-processing
    crop = 256 # image pre-processing

    lr = 1e-4 # learning rate

    # Conv Encoder
    vert_anchors = 8
    horz_anchors = 8
    anchors = vert_anchors * horz_anchors

	# GPT Encoder
    n_embd = 512
    block_exp = 4
    n_layer = 8
    n_head = 4
    n_scale = 4
    embd_pdrop = 0.1
    resid_pdrop = 0.1
    attn_pdrop = 0.1

    # Controller
    turn_KP = 1.25
    turn_KI = 0.75
    turn_KD = 0.3
    turn_n = 40 # buffer size

    speed_KP = 5.0
    speed_KI = 0.5
    speed_KD = 1.0
    speed_n = 40 # buffer size

    max_throttle = 0.75 # upper limit on throttle signal value in dataset
    brake_speed = 0.1 # desired speed below which brake is triggered
    brake_ratio = 1.1 # ratio of speed to desired speed at which brake is triggered
    clip_delta = 0.25 # maximum change in speed input to logitudinal controller


    #tambahan buat predict_expert
    model = 'transfuser'
    logdir = 'log/'+model#+'_w1'
    gpu_id = '0'
    #buat prediksi expert, test
    test_data = []
    test_weather = 'ClearNoon' #ClearNoon, ClearSunset, CloudyNoon, CloudySunset, WetNoon, WetSunset, MidRainyNoon, MidRainSunset, WetCloudyNoon, WetCloudySunset, HardRainNoon, HardRainSunset, SoftRainNoon, SoftRainSunset, Run1_ClearNoon, Run2_ClearNoon, Run3_ClearNoon
    test_scenario = 'NORMAL' #NORMAL ADVERSARIAL
    expert_dir = '/media/aisl/data/XTRANSFUSER/EXPERIMENT_RUN/8T14W/EXPERT/'+test_scenario+'/'+test_weather  #8T1W 8T14W
    for town in val_towns: #test town = val town
        test_data.append(os.path.join(expert_dir, 'Expert')) #Expert Expert_w1
    
    #untuk yang langsung dari dataset
    # test_data = ['/media/aisl/data/XTRANSFUSER/my_dataset/clear_noon_full_data/Town05_long']
    # test_data = ['/media/aisl/HDD/OSKAR/TRANSFUSER/EXPERIMENT_RUN/8T1W/EXPERT/NORMAL/Run2_ClearNoon/Expert_w1']

    def __init__(self, **kwargs):
        for k,v in kwargs.items():
            setattr(self, k, v)
