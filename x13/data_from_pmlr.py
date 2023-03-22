import os
import json
import cv2
from PIL import Image, ImageFile

import numpy as np
import torch 
from torch.utils.data import Dataset


class CARLA_Data(Dataset):

    def __init__(self, root, config):
        self.config = config
        self.seq_len = config.seq_len
        self.pred_len = config.pred_len

        self.front = []
        # self.left = []
        # self.right = []
        # self.rear = []
        self.x = []
        self.y = []
        self.x_command = []
        self.y_command = []
        self.theta = []
        self.steer = []
        self.throttle = []
        self.brake = []
        self.command = []
        self.velocity = []
        self.seg_front = []
        self.depth_front = []
        self.red_light = []
        self.stop_sign = []

        for sub_root in root:
            preload_file = os.path.join(sub_root, 'x13_rgb_dep_vel_nxr_ctrl_ts_'+str(self.seq_len)+'_'+str(self.pred_len)+'.npy')
          

            # dump to npy if no preload
            if not os.path.exists(preload_file):
                preload_front = []
                # preload_left = []
                # preload_right = []
                # preload_rear = []
                preload_x = []
                preload_y = []
                preload_x_command = []
                preload_y_command = []
                preload_theta = []
                preload_steer = []
                preload_throttle = []
                preload_brake = []
                preload_command = []
                preload_velocity = []
                preload_seg_front = []
                preload_depth_front = []
                preload_red_light = []
                preload_stop_sign = []

                # list sub-directories in root 
                root_files = os.listdir(sub_root)
                scenarios = [folder for folder in root_files if not os.path.isfile(os.path.join(sub_root,folder))]
                # for route in routes:
                #     routep = os.path.join(sub_root,route)
                #     scn_files = os.listdir(routep)
                #     scenarios = [folder for folder in scn_files if not os.path.isfile(os.path.join(routep,folder))]

                for scenario in scenarios:

                    scenario_dir = os.path.join(sub_root, scenario)
                    # print(scenario_dir)
                    # subtract final frames (pred_len) since there are no future waypoints
                    # first frame of sequence not used
                    num_seq = (len(os.listdir(scenario_dir+"/rgb/"))-self.pred_len-2)//self.seq_len
                    for seq in range(num_seq):
                        fronts = []
                        # lefts = []
                        # rights = []
                        # rears = []
                        xs = []
                        ys = []
                        thetas = []
                        seg_fronts = []
                        depth_fronts = []

                        # read files sequentially (past and current frames)
                        for i in range(self.seq_len):
                            
                            # images
                            filename = f"{str(seq*self.seq_len+1+i).zfill(4)}.png"
                            fronts.append(scenario_dir+"/rgb/"+filename)
                            # lefts.append(route_dir+"/rgb_left/"+filename)
                            # rights.append(route_dir+"/rgb_right/"+filename)
                            # rears.append(route_dir+"/rgb_rear/"+filename)
                            seg_fronts.append(scenario_dir+"/semantics/"+filename)
                            depth_fronts.append(scenario_dir+"/depth/"+filename)

                            # position
                            with open(scenario_dir + f"/measurements/{str(seq*self.seq_len+1+i).zfill(4)}.json", "r") as read_file:
                                data = json.load(read_file)
                            xs.append(data['x'])
                            ys.append(data['y'])
                            thetas.append(data['theta'])

                        # get control value of final frame in sequence
                        preload_x_command.append(data['x_command'])
                        preload_y_command.append(data['y_command'])
                        preload_steer.append(data['steer'])
                        preload_throttle.append(data['throttle'])
                        preload_brake.append(data['brake'])
                        preload_command.append(data['command'])
                        preload_velocity.append(data['speed'])
                        preload_red_light.append(data['light_hazard'])
                        preload_stop_sign.append(data['stop_sign_hazard'])
                        

                        # read files sequentially (future frames)
                        for i in range(self.seq_len, self.seq_len + self.pred_len):
                            # position
                            with open(scenario_dir + f"/measurements/{str(seq*self.seq_len+1+i).zfill(4)}.json", "r") as read_file:
                                data = json.load(read_file)
                            xs.append(data['x'])
                            ys.append(data['y'])

                            # fix for theta=nan in some measurements
                            if np.isnan(data['theta']):
                                thetas.append(0)
                            else:
                                thetas.append(data['theta'])

                        preload_front.append(fronts)
                        # preload_left.append(lefts)
                        # preload_right.append(rights)
                        # preload_rear.append(rears)
                        preload_x.append(xs)
                        preload_y.append(ys)
                        preload_theta.append(thetas)
                        preload_seg_front.append(seg_fronts)
                        preload_depth_front.append(depth_fronts)

                # dump to npy
                preload_dict = {}
                preload_dict['front'] = preload_front
                # preload_dict['left'] = preload_left
                # preload_dict['right'] = preload_right
                # preload_dict['rear'] = preload_rear
                preload_dict['x'] = preload_x
                preload_dict['y'] = preload_y
                preload_dict['x_command'] = preload_x_command
                preload_dict['y_command'] = preload_y_command
                preload_dict['theta'] = preload_theta
                preload_dict['steer'] = preload_steer
                preload_dict['throttle'] = preload_throttle
                preload_dict['brake'] = preload_brake
                preload_dict['command'] = preload_command
                preload_dict['velocity'] = preload_velocity
                preload_dict['seg_front'] = preload_seg_front
                preload_dict['depth_front'] = preload_depth_front
                preload_dict['red_light'] = preload_red_light
                preload_dict['stop_sign'] = preload_stop_sign

                np.save(preload_file, preload_dict)

            # load from npy if available
            preload_dict = np.load(preload_file, allow_pickle=True)
            self.front += preload_dict.item()['front']
            # self.left += preload_dict.item()['left']
            # self.right += preload_dict.item()['right']
            # self.rear += preload_dict.item()['rear']
            self.x += preload_dict.item()['x']
            self.y += preload_dict.item()['y']
            self.x_command += preload_dict.item()['x_command']
            self.y_command += preload_dict.item()['y_command']
            self.theta += preload_dict.item()['theta']
            self.steer += preload_dict.item()['steer']
            self.throttle += preload_dict.item()['throttle']
            self.brake += preload_dict.item()['brake']
            self.command += preload_dict.item()['command']
            self.velocity += preload_dict.item()['velocity']
            self.seg_front += preload_dict.item()['seg_front']
            self.depth_front += preload_dict.item()['depth_front']
            self.red_light += preload_dict.item()['red_light']
            self.stop_sign += preload_dict.item()['stop_sign']

            print("Preloading " + str(len(preload_dict.item()['front'])) + " sequences from " + preload_file)

    def __len__(self):
        return len(self.front)

    def __getitem__(self, index):
        data = dict()
        data['fronts'] = []
        # data['lefts'] = []
        # data['rights'] = []
        # data['rears'] = []
        data['seg_fronts'] = []
        data['depth_fronts'] = []
        seq_fronts = self.front[index]
        # seq_lefts = self.left[index]
        # seq_rights = self.right[index]
        # seq_rears = self.rear[index]
        seq_x = self.x[index]
        seq_y = self.y[index]
        seq_theta = self.theta[index]
        seq_seg_fronts = self.seg_front[index]
        seq_depth_fronts = self.depth_front[index]

        # print("=====================================================================")
        # print(seq_fronts)
        # print(seq_seg_fronts[-1])

        for i in range(self.seq_len):
            # fix for theta=nan in some measurements
            if np.isnan(seq_theta[i]):
                seq_theta[i] = 0.

        #input 1 RGB, no sequence
        data['fronts'] = torch.from_numpy(np.array(
            scale_and_crop_image(Image.open(seq_fronts[-1]), scale=self.config.scale, crop=self.config.input_resolution))) #[ ]
        data['seg_fronts'] = torch.from_numpy(np.array(cls2one_hot(
            scale_and_crop_image_cv(cv2.imread(seq_seg_fronts[-1]), scale=self.config.scale, crop=self.config.input_resolution)))) #[ ]
        data['depth_fronts'] = torch.from_numpy(np.array(rgb_to_depth(
            scale_and_crop_image_cv(swap_RGB2BGR(cv2.imread(seq_depth_fronts[-1], cv2.COLOR_BGR2RGB)), scale=self.config.scale, crop=self.config.input_resolution)))) #[ ]

        ego_x = seq_x[i]
        ego_y = seq_y[i]
        ego_theta = seq_theta[i]   

        # lidar and waypoint processing to local coordinates
        waypoints = []
        for i in range(self.seq_len + self.pred_len):
            # waypoint is the transformed version of the origin in local coordinates
            # we use 90-theta instead of theta
            # LBC code uses 90+theta, but x is to the right and y is downwards here
            local_waypoint = transform_2d_points(np.zeros((1,3)), 
                np.pi/2-seq_theta[i], -seq_x[i], -seq_y[i], np.pi/2-ego_theta, -ego_x, -ego_y)
            waypoints.append(tuple(local_waypoint[0,:2]))

        data['waypoints'] = waypoints

        # convert x_command, y_command to local coordinates
        # taken from LBC code (uses 90+theta instead of theta)
        R = np.array([
            [np.cos(np.pi/2+ego_theta), -np.sin(np.pi/2+ego_theta)],
            [np.sin(np.pi/2+ego_theta),  np.cos(np.pi/2+ego_theta)]
            ])
        local_command_point = np.array([self.x_command[index]-ego_x, self.y_command[index]-ego_y])
        local_command_point = R.T.dot(local_command_point)
        data['target_point'] = tuple(local_command_point)

        data['steer'] = self.steer[index]
        data['throttle'] = self.throttle[index]
        data['brake'] = self.brake[index]
        data['velocity'] = self.velocity[index]
        data['red_light'] = self.red_light[index]
        data['stop_sign'] = self.stop_sign[index]
        
        return data

def swap_RGB2BGR(matrix):
    red = matrix[:,:,0].copy()
    blue = matrix[:,:,2].copy()
    matrix[:,:,0] = blue
    matrix[:,:,2] = red
    return matrix

def scale_and_crop_image(image, scale=1, crop=256):
    """
    Scale and crop a PIL image, returning a channels-first numpy array.
    """
    (width, height) = (int(image.width // scale), int(image.height // scale))
    im_resized = image.resize((width, height))
    image = np.asarray(im_resized)
    start_x = height//2 - crop[0]//2
    start_y = width//2 - crop[1]//2
    cropped_image = image[start_x:start_x+crop[0], start_y:start_y+crop[1]]
    cropped_image = np.transpose(cropped_image, (2,0,1))
    return cropped_image

def scale_and_crop_image_cv(image, scale=1, crop=256):
    upper_left_yx = [int((image.shape[0]/2) - (crop[0]/2)), int((image.shape[1]/2) - (crop[1]/2))]
    cropped_im = image[upper_left_yx[0]:upper_left_yx[0]+crop[0], upper_left_yx[1]:upper_left_yx[1]+crop[1], :]
    cropped_image = np.transpose(cropped_im, (2,0,1))
    return cropped_image

def cls2one_hot(ss_gt):
    ss_gt = ss_gt[:1,:,:].reshape(ss_gt.shape[1], ss_gt.shape[2])
    result = (np.arange(23) == ss_gt[...,None]).astype(int) #len(classes_ss_su
    result = np.transpose(result, (2, 0, 1))   # (H, W, C) --> (C, H, W)
    return result


def rgb_to_depth(de_gt):
    de_gt = de_gt.transpose(1, 2, 0)
    arrayd = de_gt.astype(np.float32)
    normalized_depth = np.dot(arrayd, [65536.0, 256.0, 1.0]) # Apply (R + G * 256 + B * 256 * 256) / (256 * 256 * 256 - 1).
    depthx = normalized_depth/16777215.0  # (256.0 * 256.0 * 256.0 - 1.0) --> rangenya 0 - 1
    result = np.expand_dims(depthx, axis=0)
    return result


def transform_2d_points(xyz, r1, t1_x, t1_y, r2, t2_x, t2_y):
    """
    Build a rotation matrix and take the dot product.
    """
    # z value to 1 for rotation
    xy1 = xyz.copy()
    xy1[:,2] = 1

    c, s = np.cos(r1), np.sin(r1)
    r1_to_world = np.matrix([[c, s, t1_x], [-s, c, t1_y], [0, 0, 1]])

    # np.dot converts to a matrix, so we explicitly change it back to an array
    world = np.asarray(r1_to_world @ xy1.T)

    c, s = np.cos(r2), np.sin(r2)
    r2_to_world = np.matrix([[c, s, t2_x], [-s, c, t2_y], [0, 0, 1]])
    world_to_r2 = np.linalg.inv(r2_to_world)

    out = np.asarray(world_to_r2 @ world).T
    
    # reset z-coordinate
    out[:,2] = xyz[:,2]

    return out
