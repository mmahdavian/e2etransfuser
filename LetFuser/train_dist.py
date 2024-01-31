import pandas as pd
import os
from tqdm import tqdm
from collections import OrderedDict
import time
import numpy as np
from torch import torch, nn
import torch.optim as optim
from torch.utils.data import DataLoader
import torch.nn.functional as F
torch.backends.cudnn.benchmark = True

from model_dist import letfuser
#from model_no_attn import letfuser
from data import CARLA_Data
# from data import CARLA_Data
from config import GlobalConfig
from torch.utils.tensorboard import SummaryWriter

import wandb

class RandomSampler(object):
    def __init__(self, data_source, num_samples=None):
        self.data_source = data_source
        self._num_samples = num_samples

        if not isinstance(self.num_samples, int) or self.num_samples <= 0:
            raise ValueError(
                "num_samples should be a positive integer "
                "value, but got num_samples={}".format(self.num_samples)
            )

    @property
    def num_samples(self):
        # dataset size might change at runtime
        if self._num_samples is None:
            return len(self.data_source)
        return self._num_samples

    def __iter__(self):
        n = len(self.data_source)
        return iter(torch.randperm(n, dtype=torch.int64)[: self.num_samples].tolist())

    def __len__(self):
        return self.num_samples

class AverageMeter(object):
    def __init__(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0
    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

def BCEDice(Yp, Yt, smooth=1e-7):
	Yp = Yp.view(-1)
	Yt = Yt.view(-1)
	bce = F.binary_cross_entropy(Yp, Yt, reduction='mean')
	intersection = (Yp * Yt).sum() #irisan
	dice_loss = 1 - ((2. * intersection + smooth) / (Yp.sum() + Yt.sum() + smooth))
	bce_dice_loss = bce + dice_loss
	return bce_dice_loss


def renormalize_params_lw(current_lw, config):
	lw = np.array([tens.cpu().detach().numpy() for tens in current_lw])
	lws = np.array([lw[i][0] for i in range(len(lw))])
	coef = np.array(config.loss_weights).sum()/lws.sum()
	new_lws = [coef*lwx for lwx in lws]
	normalized_lws = [torch.cuda.FloatTensor([lw]).clone().detach().requires_grad_(True) for lw in new_lws]
	return normalized_lws

#FUNGSI TRAINING
def train(data_loader, model, config, writer, cur_epoch, device, optimizer, params_lw, optimizer_lw):
	score = {'total_loss': AverageMeter(),
			'ss_loss': AverageMeter(),
			'wp_loss': AverageMeter(),
			'str_loss': AverageMeter(),
			'thr_loss': AverageMeter(),
			'brk_loss': AverageMeter(),
			'redl_loss': AverageMeter(),
			'stops_loss': AverageMeter(),
			'speed_loss': AverageMeter()}
	
	model.train()
	prog_bar = tqdm(total=len(data_loader))

	#training...
	total_batch = len(data_loader)
	print("data amount is:")
	print(total_batch)
	batch_ke = 0
	for data in data_loader:
		cur_step = cur_epoch*total_batch + batch_ke
		fronts = data['fronts'].to(device, dtype=torch.float) #ambil yang terakhir aja #[-1]
		seg_fronts = data['seg_fronts'].to(device, dtype=torch.float) #ambil yang terakhir aja #[-1]
		depth_fronts = data['depth_fronts'].to(device, dtype=torch.float) #ambil yang terakhir aja #[-1]
		target_point = torch.stack(data['target_point'], dim=1).to(device, dtype=torch.float)
		gt_velocity = data['velocity'].to(device, dtype=torch.float)
		gt_waypoints = [torch.stack(data['waypoints'][i], dim=1).to(device, dtype=torch.float) for i in range(config.seq_len, len(data['waypoints']))]
		gt_waypoints = torch.stack(gt_waypoints, dim=1).to(device, dtype=torch.float)
		
		# correct the nan in GT steer
		if config.augment_control_data:
			gt_steer = [data['steer'][i].to(device, dtype=torch.float) for i in range(len(data['steer']))]
			gt_steer = torch.stack(gt_steer, dim=1).to(device, dtype=torch.float)
			gt_steer = torch.nan_to_num(gt_steer)
			gt_throttle = [data['throttle'][i].to(device, dtype=torch.float) for i in range(len(data['throttle']))]
			gt_throttle = torch.stack(gt_throttle, dim=1).to(device, dtype=torch.float)
			gt_brake = [data['brake'][i].to(device, dtype=torch.float) for i in range( len(data['brake']))]
			gt_brake = torch.stack(gt_brake, dim=1).to(device, dtype=torch.float)

		else:
			gt_steer = data['steer'][0].to(device, dtype=torch.float)
			gt_steer = torch.nan_to_num(gt_steer) if any(torch.isnan(gt_steer)) else gt_steer
			gt_throttle = data['throttle'][0].to(device, dtype=torch.float)
			gt_brake = data['brake'][0].to(device, dtype=torch.float)
		gt_red_light = data['red_light'].to(device, dtype=torch.float)
		gt_stop_sign = data['stop_sign'].to(device, dtype=torch.float)
		gt_command = data['command'].to(device, dtype=torch.float)

		#forward pass
		pred_seg, pred_wp, steer, throttle, brake, red_light, stop_sign, _,speed,D_pred_wp, D_steer, D_throttle, l1= model(fronts, depth_fronts, target_point, gt_velocity, gt_command)

		if cur_epoch< config.cvt_freezed_epoch and list(model.named_parameters())[0][1].requires_grad: # freeze CVT
			if list(model.named_parameters())[0][0][:3] != 'cvt' :
				raise Exception("The order number of the CVT is changed in the arch, please consider changing accordingly")
			for name0, param0 in model.named_parameters():
				if 'cvt' ==name0[:3]:
					param0.requires_grad = False
			# # a slightly faster approach, but index might mismatch if the arch. is changed.
			# for index ,(_, param0) in enumerate(model.named_parameters()):
			# 	if index<= 337: # all  the CVT layers
			# 		param0.requires_grad = False
		elif cur_epoch == config.cvt_freezed_epoch and not list(model.named_parameters())[0][1].requires_grad: # start fine tuning CVT
			for name0, param0 in model.named_parameters():
				if 'cvt' ==name0[:3]:
					param0.requires_grad = True
			# # a slightly faster approach, but index might mismatch if the arch. is changed.
			# for index ,(_, param0) in enumerate(model.named_parameters()):
			# 	if index<= 337: # all  the CVT layers
			# 		param0.requires_grad = True


		#compute loss
		loss_seg = BCEDice(pred_seg, seg_fronts)
		loss_wp = F.l1_loss(pred_wp, gt_waypoints) +F.l1_loss(D_pred_wp, gt_waypoints)
		loss_str = F.l1_loss(steer, gt_steer) + F.l1_loss(D_steer , gt_steer)
		loss_thr = F.l1_loss(throttle, gt_throttle)
		loss_brk = F.l1_loss(brake, gt_brake) + F.l1_loss(D_throttle, gt_brake) 
		loss_redl = F.l1_loss(red_light, gt_red_light)
		#loss_stops = F.l1_loss(stop_sign, gt_stop_sign)
		loss_stops = 0.001* np.sqrt(0.2*cur_epoch+0.04)* l1 # new definition of stops
		loss_speed = F.l1_loss(speed.squeeze(-1), gt_velocity)
		total_loss = params_lw[0]*loss_seg + params_lw[1]*loss_wp + params_lw[2]*loss_str + params_lw[3]*loss_thr + params_lw[4]*loss_brk + params_lw[5]*loss_redl + params_lw[6]* loss_stops +params_lw[7]* loss_speed
		optimizer.zero_grad()

		if batch_ke == 0: #first batch, calculate the initial loss
			total_loss.backward() #no need to retain the graph
			loss_seg_0 = torch.clone(loss_seg)
			loss_wp_0 = torch.clone(loss_wp)
			loss_str_0 = torch.clone(loss_str)
			loss_thr_0 = torch.clone(loss_thr)
			loss_brk_0 = torch.clone(loss_brk)
			loss_redl_0 = torch.clone(loss_redl)
			loss_stops_0 = torch.clone(loss_stops)
			loss_speed_0 = torch.clone(loss_speed)
		elif 0 < batch_ke < total_batch-1:
			total_loss.backward() #no need to retain the graph

		elif batch_ke == total_batch-1: #berarti batch terakhir, compute update loss weights
			if config.MGN:
				optimizer_lw.zero_grad()
				total_loss.backward(retain_graph=True) # retain graph because the graph is still used for calculation
				params = list(filter(lambda p: p.requires_grad, model.parameters()))
				
                # in case of model freeze, we should change the bottleneck values
				d = len(list(model.parameters()))-len(params)
				if d: # we have freezed parameters in the front
					G0, G1, G2, G3, G4, G5, G6 = None, None,None,None,None,None,None
					if config.bottleneck[2]-d>=0:
						G0R = torch.autograd.grad(loss_seg, params[config.bottleneck[2]-d], retain_graph=True, create_graph=True)
						G0 = torch.norm(G0R[0], keepdim=True)
					if config.bottleneck[1]-d>=0:
						G1R = torch.autograd.grad(loss_wp, params[config.bottleneck[1]-d], retain_graph=True, create_graph=True ) #, allow_unused=True
						G1 = torch.norm(G1R[0], keepdim=True)
						G2R = torch.autograd.grad(loss_str, params[config.bottleneck[1]-d], retain_graph=True, create_graph=True)
						G2 = torch.norm(G2R[0], keepdim=True)
						G3R = torch.autograd.grad(loss_thr, params[config.bottleneck[1]-d], retain_graph=True, create_graph=True)
						G3 = torch.norm(G3R[0], keepdim=True)
						G4R = torch.autograd.grad(loss_brk, params[config.bottleneck[1]-d], retain_graph=True, create_graph=True)
						G4 = torch.norm(G4R[0], keepdim=True)
					if config.bottleneck[0]-d>=0:
						G5R = torch.autograd.grad(loss_redl, params[config.bottleneck[0]-d], retain_graph=True, create_graph=True)
						G5 = torch.norm(G5R[0], keepdim=True)
						# G6R = torch.autograd.grad(loss_stops, params[config.bottleneck[0]], retain_graph=True, create_graph=True)
						# G6 = torch.norm(G6R[0], keepdim=True)
						G6R = torch.autograd.grad(loss_stops, params[config.bottleneck[0]-d], retain_graph=True, create_graph=True)
						G6 = torch.norm(G6R[0], keepdim=True) # distilation
						G7R = torch.autograd.grad(loss_speed, params[config.bottleneck[0]-d], retain_graph=True, create_graph=True)
						G7 = torch.norm(G7R[0], keepdim=True)
					
					if not G0:
						G0 = torch.zeros_like(G5)
					if not G6:
						G6 = torch.zeros_like(G5) # we don't have stop sign
				
				else:
					G0R = torch.autograd.grad(loss_seg, params[config.bottleneck[2]], retain_graph=True, create_graph=True)
					G0 = torch.norm(G0R[0], keepdim=True)
					G1R = torch.autograd.grad(loss_wp, params[config.bottleneck[1]], retain_graph=True, create_graph=True ) #, allow_unused=True
					G1 = torch.norm(G1R[0], keepdim=True)
					G2R = torch.autograd.grad(loss_str, params[config.bottleneck[1]], retain_graph=True, create_graph=True)
					G2 = torch.norm(G2R[0], keepdim=True)
					G3R = torch.autograd.grad(loss_thr, params[config.bottleneck[1]], retain_graph=True, create_graph=True)
					G3 = torch.norm(G3R[0], keepdim=True)
					G4R = torch.autograd.grad(loss_brk, params[config.bottleneck[1]], retain_graph=True, create_graph=True)
					G4 = torch.norm(G4R[0], keepdim=True)
					G5R = torch.autograd.grad(loss_redl, params[config.bottleneck[0]], retain_graph=True, create_graph=True)
					G5 = torch.norm(G5R[0], keepdim=True)
					# G6R = torch.autograd.grad(loss_stops, params[config.bottleneck[0]], retain_graph=True, create_graph=True)
					# G6 = torch.norm(G6R[0], keepdim=True)
					G6R = torch.autograd.grad(loss_stops, params[config.bottleneck[0]], retain_graph=True, create_graph=True)
					G6 = torch.norm(G6R[0], keepdim=True) # distilation
					# G6 = torch.zeros_like(G5) # we don't have stop sign
					G7R = torch.autograd.grad(loss_speed, params[config.bottleneck[0]], retain_graph=True, create_graph=True)
					G7 = torch.norm(G7R[0], keepdim=True)
				G_avg = (G0+G1+G2+G3+G4+G5+G6+G7) / len(config.loss_weights)

				#relative loss (zero division handling)
				loss_seg_hat = loss_seg / loss_seg_0  if loss_seg_0 else loss_seg
				loss_wp_hat = loss_wp / loss_wp_0 if loss_wp_0 else loss_wp
				loss_str_hat = loss_str / loss_str_0  if loss_str_0 else loss_str
				loss_thr_hat = loss_thr / loss_thr_0  if loss_thr_0 else loss_thr
				loss_brk_hat = loss_brk / loss_brk_0  if loss_brk_0 else loss_brk
				loss_redl_hat = loss_redl / loss_redl_0 if loss_redl_0 else loss_redl
				loss_stops_hat = loss_stops / loss_stops_0 if loss_stops_0 else loss_stops
				loss_speed_hat = loss_speed / loss_speed_0 if loss_speed_0 else loss_speed
				loss_hat_avg = (loss_seg_hat + loss_wp_hat + loss_str_hat + loss_thr_hat + loss_brk_hat + loss_redl_hat + loss_stops_hat + loss_speed_hat) / len(config.loss_weights)

				#r_i_(t) relative inverse training rate
				inv_rate_ss = loss_seg_hat / loss_hat_avg
				inv_rate_wp = loss_wp_hat / loss_hat_avg
				inv_rate_str = loss_str_hat / loss_hat_avg
				inv_rate_thr = loss_thr_hat / loss_hat_avg
				inv_rate_brk = loss_brk_hat / loss_hat_avg
				inv_rate_redl = loss_redl_hat / loss_hat_avg
				inv_rate_stops = loss_stops_hat / loss_hat_avg
				inv_rate_speed = loss_speed_hat / loss_hat_avg

				#hitung constant target grad
				C0 = (G_avg*inv_rate_ss).detach().squeeze(-1).squeeze(-1)**config.lw_alpha
				C1 = (G_avg*inv_rate_wp).detach()**config.lw_alpha
				C2 = (G_avg*inv_rate_str).detach()**config.lw_alpha
				C3 = (G_avg*inv_rate_thr).detach()**config.lw_alpha
				C4 = (G_avg*inv_rate_brk).detach()**config.lw_alpha
				C5 = (G_avg*inv_rate_redl).detach().squeeze(-1).squeeze(-1)**config.lw_alpha
				C6 = (G_avg*inv_rate_stops).detach().squeeze(-1).squeeze(-1)**config.lw_alpha
				C7 = (G_avg*inv_rate_speed).detach().squeeze(-1).squeeze(-1)**config.lw_alpha
				Lgrad = F.l1_loss(G0, C0) + F.l1_loss(G1, C1) + F.l1_loss(G2, C2) + F.l1_loss(G3, C3) + F.l1_loss(G4, C4) + F.l1_loss(G5, C5) + F.l1_loss(G6, C6)+ F.l1_loss(G7, C7)

				#hitung gradient loss sesuai Eq. 2 di GradNorm paper
				Lgrad.backward()
				optimizer_lw.step() 
				lgrad = Lgrad.item()
				new_param_lw = optimizer_lw.param_groups[0]['params']
			else:
				total_loss.backward(retain_graph=True)
				lgrad = 0
				new_param_lw = 1
		optimizer.step() 

		score['total_loss'].update(total_loss.item())
		score['ss_loss'].update(loss_seg.item()) 
		score['wp_loss'].update(loss_wp.item())
		score['str_loss'].update(loss_str.item())
		score['thr_loss'].update(loss_thr.item())
		score['brk_loss'].update(loss_brk.item())
		score['redl_loss'].update(loss_redl.item())
		score['stops_loss'].update(loss_stops.item())
		score['speed_loss'].update(loss_speed.item())

		postfix = OrderedDict([('t_total_l', score['total_loss'].avg),
							('t_ss_l', score['ss_loss'].avg),
							('t_wp_l', score['wp_loss'].avg),
							('t_str_l', score['str_loss'].avg),
							('t_thr_l', score['thr_loss'].avg),
							('t_brk_l', score['brk_loss'].avg),
							('t_redl_l', score['redl_loss'].avg),
							('t_stops_l', score['stops_loss'].avg),
							('t_speed_l', score['speed_loss'].avg)])
		
		writer.add_scalar('t_total_l', total_loss.item(), cur_step)
		writer.add_scalar('t_ss_l', loss_seg.item(), cur_step)
		writer.add_scalar('t_wp_l', loss_wp.item(), cur_step)
		writer.add_scalar('t_str_l', loss_str.item(), cur_step)
		writer.add_scalar('t_thr_l', loss_thr.item(), cur_step)
		writer.add_scalar('t_brk_l', loss_brk.item(), cur_step)
		writer.add_scalar('t_redl_l', loss_redl.item(), cur_step)
		writer.add_scalar('t_stops_l', loss_stops.item(), cur_step)
		writer.add_scalar('t_speed_l', loss_speed.item(), cur_step)

		prog_bar.set_postfix(postfix)
		prog_bar.update(1)
		batch_ke += 1
	prog_bar.close()
	# print('end train epoch')	

	#return value
	return postfix, new_param_lw, lgrad

def validate(data_loader, model, config, writer, cur_epoch, device):
	score = {'total_loss': AverageMeter(),
			'ss_loss': AverageMeter(),
			'wp_loss': AverageMeter(),
			'str_loss': AverageMeter(),
			'thr_loss': AverageMeter(),
			'brk_loss': AverageMeter(),
			'redl_loss': AverageMeter(),
                        'stops_loss': AverageMeter(),
			'speed_loss': AverageMeter()}
			
	model.eval()

	with torch.no_grad():
		prog_bar = tqdm(total=len(data_loader))

		#validation....
		total_batch = len(data_loader)
		batch_ke = 0
		for data in data_loader:
			cur_step = cur_epoch*total_batch + batch_ke
			fronts = data['fronts'].to(device, dtype=torch.float) #ambil yang terakhir aja #[-1]
			seg_fronts = data['seg_fronts'].to(device, dtype=torch.float) #ambil yang terakhir aja #[-1]
			depth_fronts = data['depth_fronts'].to(device, dtype=torch.float) #ambil yang terakhir aja #[-1]
			target_point = torch.stack(data['target_point'], dim=1).to(device, dtype=torch.float)
			gt_velocity = data['velocity'].to(device, dtype=torch.float)
			gt_waypoints = [torch.stack(data['waypoints'][i], dim=1).to(device, dtype=torch.float) for i in range(config.seq_len, len(data['waypoints']))]
			gt_waypoints = torch.stack(gt_waypoints, dim=1).to(device, dtype=torch.float)
			
			if config.augment_control_data:
				gt_steer = [data['steer'][i].to(device, dtype=torch.float) for i in range(len(data['steer']))]
				gt_steer = torch.stack(gt_steer, dim=1).to(device, dtype=torch.float)
				gt_steer = torch.nan_to_num(gt_steer)
				gt_throttle = [data['throttle'][i].to(device, dtype=torch.float) for i in range(len(data['throttle']))]
				gt_throttle = torch.stack(gt_throttle, dim=1).to(device, dtype=torch.float)
				gt_brake = [data['brake'][i].to(device, dtype=torch.float) for i in range( len(data['brake']))]
				gt_brake = torch.stack(gt_brake, dim=1).to(device, dtype=torch.float)
			else:
				gt_steer = data['steer'][0].to(device, dtype=torch.float)
				gt_steer = torch.nan_to_num(gt_steer) if any(torch.isnan(gt_steer)) else gt_steer
				gt_throttle = data['throttle'][0].to(device, dtype=torch.float)
				gt_brake = data['brake'][0].to(device, dtype=torch.float)


			gt_red_light = data['red_light'].to(device, dtype=torch.float)
			gt_stop_sign = data['stop_sign'].to(device, dtype=torch.float)
			gt_command = data['command'].to(device, dtype=torch.float)

			#forward pass
			pred_seg, pred_wp, steer, throttle, brake, red_light, stop_sign, _,speed, D_pred_wp, D_steer, D_throttle, l1 = model(fronts, depth_fronts, target_point, gt_velocity, gt_command)

			#compute loss
			loss_seg = BCEDice(pred_seg, seg_fronts)
			loss_wp = F.l1_loss(pred_wp, gt_waypoints)

			
			if config.augment_control_data:
				# To be consistent with non-augment appraoches only compute the first error
				loss_str = F.l1_loss(steer[:,0], gt_steer[:,0])
				loss_thr = F.l1_loss(throttle[:,0], gt_throttle[:,0])
				loss_brk = F.l1_loss(brake[:,0], gt_brake[:,0])
			else:
				loss_str = F.l1_loss(steer, gt_steer)
				loss_thr = F.l1_loss(throttle, gt_throttle)
				loss_brk = F.l1_loss(brake, gt_brake)

			loss_redl = F.l1_loss(red_light, gt_red_light)
			loss_stops = F.l1_loss(stop_sign, gt_stop_sign)
			loss_speed = F.l1_loss(speed.squeeze(-1), gt_velocity)

			#total_loss = loss_seg + loss_wp + loss_str + loss_thr + loss_brk + loss_redl + loss_stops + loss_speed
			total_loss = loss_seg + loss_wp + F.l1_loss(steer, gt_steer) \
											+ F.l1_loss(throttle, gt_throttle) \
											+ F.l1_loss(brake, gt_brake) \
											+ loss_redl + loss_stops + loss_speed \
											+  F.l1_loss(D_pred_wp, gt_waypoints) \
											+  F.l1_loss(D_steer, gt_steer) \
											+  F.l1_loss(D_throttle, gt_throttle)	+ l1 #additional term for dist

			score['total_loss'].update(total_loss.item())
			score['ss_loss'].update(loss_seg.item()) 
			score['wp_loss'].update(loss_wp.item())
			score['str_loss'].update(loss_str.item())
			score['thr_loss'].update(loss_thr.item())
			score['brk_loss'].update(loss_brk.item())
			score['redl_loss'].update(loss_redl.item())
			score['stops_loss'].update(loss_stops.item())
			score['speed_loss'].update(loss_speed.item())

			postfix = OrderedDict([('v_total_l', score['total_loss'].avg),
								('v_ss_l', score['ss_loss'].avg),
								('v_wp_l', score['wp_loss'].avg),
								('v_str_l', score['str_loss'].avg),
								('v_thr_l', score['thr_loss'].avg),
								('v_brk_l', score['brk_loss'].avg),
								('v_redl_l', score['redl_loss'].avg),
								('v_stops_l', score['stops_loss'].avg),
								('v_speed_l', score['speed_loss'].avg)])
			
			writer.add_scalar('v_total_l', total_loss.item(), cur_step)
			writer.add_scalar('v_ss_l', loss_seg.item(), cur_step)
			writer.add_scalar('v_wp_l', loss_wp.item(), cur_step)
			writer.add_scalar('v_str_l', loss_str.item(), cur_step)
			writer.add_scalar('v_thr_l', loss_thr.item(), cur_step)
			writer.add_scalar('v_brk_l', loss_brk.item(), cur_step)
			writer.add_scalar('v_redl_l', loss_redl.item(), cur_step)
			writer.add_scalar('v_stops_l', loss_stops.item(), cur_step)
			writer.add_scalar('v_speed_l', loss_speed.item(), cur_step)

			prog_bar.set_postfix(postfix)
			prog_bar.update(1)
			batch_ke += 1
		prog_bar.close()
		# print('end val epoch')	

	#return value
	return postfix

#MAIN FUNCTION
def main():
	config = GlobalConfig()
	if config.wandb:
		wandb.init(project=config.wandb_name,  entity="transfuser", name = config.model)
	#	wandb.init(project=config.wandb_name , entity="marslab", name = config.model)
	torch.backends.cudnn.benchmark = True
	device = torch.device("cuda:0")
	os.environ["CUDA_DEVICE_ORDER"]="PCI_BUS_ID" 
	os.environ["CUDA_VISIBLE_DEVICES"]=config.gpu_id   #visible_gpu #"0" "1" "0,1"

	#IMPORT MODEL
	print("IMPORT ARSITEKTUR DL DAN COMPILE")
	model = letfuser(config, device).float().to(device)
	model_parameters = filter(lambda p: p.requires_grad, model.parameters())
	params = sum([np.prod(p.size()) for p in model_parameters])
	print('Total trainable parameters: ', params)

	#OPTIMIZER
	optima = optim.AdamW(model.parameters(), lr=config.lr, weight_decay=config.weight_decay)
	scheduler = optim.lr_scheduler.ReduceLROnPlateau(optima, mode='min', factor=0.5, patience=config.lr_patience, min_lr=1e-6)

	#CREATE DATA BATCH
	train_set = CARLA_Data(root=config.train_data, config=config)
	val_set = CARLA_Data(root=config.val_data, config=config)
	if len(train_set)%config.batch_size == 1:
		drop_last = True 
	else: 
		drop_last = False
	
	dataloader_train = DataLoader(train_set, batch_size=config.batch_size, shuffle=True, num_workers=config.num_worker, pin_memory=True, drop_last=drop_last) 
#	dataloader_train = DataLoader(train_set, batch_size=config.batch_size, num_workers=config.num_worker, pin_memory=True, drop_last=drop_last,sampler=RandomSampler(torch.randint(high=280000, size=(config.random_data_len,)),config.random_data_len))
	dataloader_val = DataLoader(val_set, batch_size=config.batch_size, shuffle=False, num_workers=config.num_worker, pin_memory=True)
	
	if not os.path.exists(config.logdir+"/trainval_log.csv"):
		print('TRAIN from the beginning!!!!!!!!!!!!!!!!')
		os.makedirs(config.logdir, exist_ok=True)
		print('Created dir:', config.logdir)
		params_lw = [torch.cuda.FloatTensor([config.loss_weights[i]]).clone().detach().requires_grad_(True) for i in range(len(config.loss_weights))]
		optima_lw = optim.SGD(params_lw, lr=config.lr)
		curr_ep = 0
		lowest_score = float('inf')
		stop_count = config.init_stop_counter
	else:
		print('Continue training!!!!!!!!!!!!!!!!')
		print('Loading checkpoint from ' + config.logdir)
		log_trainval = pd.read_csv(config.logdir+"/trainval_log.csv")
		curr_ep = int(log_trainval['epoch'][-1:]) + 1
		lowest_score = float(np.min(log_trainval['val_loss']))
		stop_count = int(log_trainval['stop_counter'][-1:])
		model.load_state_dict(torch.load(os.path.join(config.logdir, 'recent_model.pth')))
		optima.load_state_dict(torch.load(os.path.join(config.logdir, 'recent_optim.pth')))
		latest_lw = [float(log_trainval['lw_ss'][-1:]), float(log_trainval['lw_wp'][-1:]), float(log_trainval['lw_str'][-1:]), float(log_trainval['lw_thr'][-1:]), float(log_trainval['lw_brk'][-1:]), float(log_trainval['lw_redl'][-1:]),float(log_trainval['lw_stops'][-1:]), float(log_trainval['lw_speed'][-1:])]
		params_lw = [torch.cuda.FloatTensor([latest_lw[i]]).clone().detach().requires_grad_(True) for i in range(len(latest_lw))]
		optima_lw = optim.SGD(params_lw, lr=float(log_trainval['lrate'][-1:]))
		config.logdir += "/retrain"
		os.makedirs(config.logdir, exist_ok=True)
		print('Created new retrain dir:', config.logdir)

	# dictionary log 
	log = OrderedDict([
			('epoch', []),
			('best_model', []),
			('val_loss', []),
			('val_ss_loss', []),
			('val_wp_loss', []),
			('val_str_loss', []),
			('val_thr_loss', []),
			('val_brk_loss', []),
			('val_redl_loss', []),
            ('val_stops_loss', []),
			('val_speed_loss', []),
			('train_loss', []), 
			('train_ss_loss', []),
			('train_wp_loss', []),
			('train_str_loss', []),
			('train_thr_loss', []),
			('train_brk_loss', []),
			('train_redl_loss', []),
			('train_stops_loss', []),
			('train_speed_loss', []),
			('lrate', []),
			('stop_counter', []), 
			('lgrad_loss', []),
			('lw_ss', []),
			('lw_wp', []),
			('lw_str', []),
			('lw_thr', []),
			('lw_brk', []),
			('lw_redl', []),
			('lw_stops', []),
			('lw_speed', []),
			('elapsed_time', []),
		])
	writer = SummaryWriter(log_dir=config.logdir)
	
	# if config.wandb:
	# 	wandb.watch(model, log="all")

	epoch = curr_ep
	while epoch<=config.total_epoch:
		print("Epoch: {:05d}------------------------------------------------".format(epoch))
		if config.MGN:
			curr_lw = optima_lw.param_groups[0]['params']
			lw = np.array([tens.cpu().detach().numpy() for tens in curr_lw])
			lws = np.array([lw[i][0] for i in range(len(lw))])
			print("current loss weights: ", lws)	
		else:
			curr_lw = config.loss_weights
			lws = config.loss_weights
			print("current loss weights: ", config.loss_weights)
		print("current lr for training: ", optima.param_groups[0]['lr'])

		#training validation
		start_time = time.time()
		train_log, new_params_lw, lgrad = train(dataloader_train, model, config, writer, epoch, device, optima, curr_lw, optima_lw)
		val_log = validate(dataloader_val, model, config, writer, epoch, device)
		if config.MGN:
			optima_lw.param_groups[0]['params'] = renormalize_params_lw(new_params_lw, config) #have to be cloned so that they are completely separated
			print("total loss gradient: "+str(lgrad))
		scheduler.step(val_log['v_total_l']) #the reference parameter to reduce LR is val_total_metric
		optima_lw.param_groups[0]['lr'] = optima.param_groups[0]['lr'] #update lr 
		elapsed_time = time.time() - start_time #calc elapsedtime

		log['epoch'].append(epoch)
		log['lrate'].append(optima.param_groups[0]['lr'])
		log['train_loss'].append(train_log['t_total_l'])
		log['val_loss'].append(val_log['v_total_l'])
		log['train_ss_loss'].append(train_log['t_ss_l'])
		log['val_ss_loss'].append(val_log['v_ss_l'])
		log['train_wp_loss'].append(train_log['t_wp_l'])
		log['val_wp_loss'].append(val_log['v_wp_l'])
		log['train_str_loss'].append(train_log['t_str_l'])
		log['val_str_loss'].append(val_log['v_str_l'])
		log['train_thr_loss'].append(train_log['t_thr_l'])
		log['val_thr_loss'].append(val_log['v_thr_l'])
		log['train_brk_loss'].append(train_log['t_brk_l'])
		log['val_brk_loss'].append(val_log['v_brk_l'])
		log['train_redl_loss'].append(train_log['t_redl_l'])
		log['val_redl_loss'].append(val_log['v_redl_l'])
		log['train_stops_loss'].append(train_log['t_stops_l'])
		log['val_stops_loss'].append(val_log['v_stops_l'])
		log['train_speed_loss'].append(train_log['t_speed_l'])
		log['val_speed_loss'].append(val_log['v_speed_l'])
		log['lgrad_loss'].append(lgrad)
		log['lw_ss'].append(lws[0])
		log['lw_wp'].append(lws[1])
		log['lw_str'].append(lws[2])
		log['lw_thr'].append(lws[3])
		log['lw_brk'].append(lws[4])
		log['lw_redl'].append(lws[5])
		log['lw_stops'].append(lws[6])
		log['lw_speed'].append(lws[7])
		log['elapsed_time'].append(elapsed_time)
		print('| t_total_l: %.4f | t_ss_l: %.4f | t_wp_l: %.4f | t_str_l: %.4f | t_thr_l: %.4f | t_brk_l: %.4f | t_redl_l: %.4f | t_stops_l: %.4f | t_speed_l: %.4f |' % (train_log['t_total_l'], train_log['t_ss_l'], train_log['t_wp_l'], train_log['t_str_l'], train_log['t_thr_l'], train_log['t_brk_l'], train_log['t_redl_l'], train_log['t_stops_l'], train_log['t_speed_l']))
		print('| v_total_l: %.4f | v_ss_l: %.4f | v_wp_l: %.4f | v_str_l: %.4f | v_thr_l: %.4f | v_brk_l: %.4f | v_redl_l: %.4f | v_stops_l: %.4f | v_speed_l: %.4f |' % (val_log['v_total_l'], val_log['v_ss_l'], val_log['v_wp_l'], val_log['v_str_l'], val_log['v_thr_l'], val_log['v_brk_l'], val_log['v_redl_l'], val_log['v_stops_l'], val_log['v_speed_l']))
		print('elapsed time: %.4f sec' % (elapsed_time))

		#save recent model
		torch.save(model.state_dict(), os.path.join(config.logdir, 'recent_model.pth'))
		torch.save(optima.state_dict(), os.path.join(config.logdir, 'recent_optim.pth'))

		if config.wandb:
			dic = {x: v[-1] for x,v in log.items() if v }
			wandb.log(dic)

		#save model best only
		if val_log['v_total_l'] < lowest_score:
			print("v_total_l: %.4f < lowest sebelumnya: %.4f" % (val_log['v_total_l'], lowest_score))
			print("model terbaik disave!")
			torch.save(model.state_dict(), os.path.join(config.logdir, 'best_model.pth'))
			torch.save(optima.state_dict(), os.path.join(config.logdir, 'best_optim.pth'))
			lowest_score = val_log['v_total_l']
			stop_count = config.init_stop_counter
			print("stop counter direset ke: ", stop_count)
			log['best_model'].append("BEST")
		else:
			print("v_total_l: %.4f >= lowest sebelumnya: %.4f" % (val_log['v_total_l'], lowest_score))
			print("model tidak disave!")
			stop_count -= 1
			print("stop counter : ", stop_count)
			log['best_model'].append("")

		#update stop counter
		log['stop_counter'].append(stop_count)
		pd.DataFrame(log).to_csv(os.path.join(config.logdir, 'trainval_log.csv'), index=False)
		torch.cuda.empty_cache()
		epoch += 1

		# early stopping 
		if stop_count==0:
			print("TRAINING STOPPED BECAUSE THERE IS NO DECREASE IN TOTAL LOSS IN THE LAST %d EPOCH" % (config.init_stop_counter))
			break #loop
		

if __name__ == "__main__":
	main()
