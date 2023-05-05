from collections import deque, OrderedDict
import sys
import numpy as np
from torch import torch, cat, add, nn
import torch.nn.functional as F
import torchvision.models as models
import torchvision.transforms as transforms
import matplotlib.pyplot as plt
from einops import rearrange
from einops.layers.torch import Rearrange
from timm.models.layers import DropPath, trunc_normal_
import time
import os
import cv2
from torchvision.transforms.functional import rotate

#from transformers import CvtModel #, AutoImageProcessor


def kaiming_init_layer(layer):
    nn.init.kaiming_normal_(layer.weight, nonlinearity='relu')

def kaiming_init(m):
    if isinstance(m, nn.Conv2d):
        nn.init.kaiming_normal_(m.weight, nonlinearity='relu')
    elif isinstance(m, nn.Linear):
        nn.init.kaiming_normal_(m.weight, nonlinearity='relu')

class ConvBNRelu(nn.Module):
    def __init__(self, channelx, stridex=1, kernelx=3, paddingx=1):
        super(ConvBNRelu, self).__init__()
        self.conv = nn.Conv2d(channelx[0], channelx[1], kernel_size=kernelx, stride=stridex, padding=paddingx, padding_mode='zeros')
        self.bn = nn.BatchNorm2d(channelx[1])
        self.relu = nn.ReLU()
    
    def forward(self, x):
        x = self.conv(x) 
        x = self.bn(x) 
        y = self.relu(x)
        return y

class ConvBlock(nn.Module):
    def __init__(self, channel, final=False): #up, 
        super(ConvBlock, self).__init__()
        if final:
            self.conv_block0 = ConvBNRelu(channelx=[channel[0], channel[0]], stridex=1)
            self.conv_block1 = nn.Sequential(
            nn.Conv2d(channel[0], channel[1], kernel_size=1),
            nn.Sigmoid()
            )
        else:
            self.conv_block0 = ConvBNRelu(channelx=[channel[0], channel[1]], stridex=1)
            self.conv_block1 = ConvBNRelu(channelx=[channel[1], channel[1]], stridex=1)
        self.conv_block0.apply(kaiming_init)
        self.conv_block1.apply(kaiming_init)
 
    def forward(self, x):
        y = self.conv_block0(x)
        y = self.conv_block1(y)
        return y


class PIDController(object):
    def __init__(self, K_P=1.0, K_I=0.0, K_D=0.0, n=20):
        self._K_P = K_P
        self._K_I = K_I
        self._K_D = K_D
        self._window = deque([0 for _ in range(n)], maxlen=n)
        self._max = 0.0
        self._min = 0.0
    
    def step(self, error):
        self._window.append(error)
        self._max = max(self._max, abs(error))
        self._min = -abs(self._max)
        if len(self._window) >= 2:
            integral = np.mean(self._window)
            derivative = (self._window[-1] - self._window[-2])
        else:
            integral = 0.0
            derivative = 0.0
        out_control = self._K_P * error + self._K_I * integral + self._K_D * derivative
        return out_control
    
class Mlp(nn.Module):
    def __init__(self,
                 in_features,
                 hidden_features=None,
                 out_features=None,
                 act_layer=nn.GELU,
                 drop=0.):
        super().__init__()
        out_features = out_features or in_features
        hidden_features = hidden_features or in_features
        self.fc1 = nn.Linear(in_features, hidden_features)
        self.act = act_layer()
        self.fc2 = nn.Linear(hidden_features, out_features)
        self.drop = nn.Dropout(drop)

    def forward(self, x):
        x = self.fc1(x)
        x = self.act(x)
        x = self.drop(x)
        x = self.fc2(x)
        x = self.drop(x)
        return x

class Attention_2D(nn.Module):
    def __init__(self,
                 dim_q,
                 dim_kv,
                 num_heads,
                 qkv_bias=False,
                 attn_drop=0.,
                 proj_drop=0.,
                 method='dw_bn',
                 kernel_size=3,
                 stride_kv=1,
                 stride_q=1,
                 padding_kv=1,
                 padding_q=1,
                 with_cls_token=False,
                 ):
        super().__init__()
        self.stride_kv = stride_kv
        self.stride_q = stride_q
        self.dim = dim_q
        self.num_heads = num_heads
        # head_dim = self.qkv_dim // num_heads
        self.scale = dim_q ** -0.5
        self.with_cls_token = with_cls_token

        self.conv_proj_q = self._build_projection(
            dim_q, dim_q, kernel_size, padding_q,
            1, 'linear' if method == 'avg' else method
        )
        self.conv_proj_k = self._build_projection(
            dim_kv, dim_q, kernel_size, padding_kv,
            1, method
        )
        self.conv_proj_v = self._build_projection(
            dim_kv, dim_q, kernel_size, padding_kv,
            1, method
        )

        self.proj_q = nn.Linear(dim_q, dim_q, bias=qkv_bias)
        self.proj_k = nn.Linear(dim_q, dim_q, bias=qkv_bias)
        self.proj_v = nn.Linear(dim_q, dim_q, bias=qkv_bias)

        self.attn_drop = nn.Dropout(attn_drop)
        self.proj = nn.Linear(dim_q, dim_q)
        self.proj_drop = nn.Dropout(proj_drop)

    def _build_projection(self,
                          dim_in,
                          dim_out,
                          kernel_size,
                          padding,
                          stride,
                          method):
        if method == 'dw_bn':
            proj = nn.Sequential(OrderedDict([
                ('conv', nn.Conv2d(
                    dim_in,
                    dim_out,
                    kernel_size=kernel_size,
                    padding=padding,
                    stride=stride,
                    bias=False,
                    groups=1 #dim_in
                )),
                ('bn', nn.BatchNorm2d(dim_out)),
                ('rearrage', Rearrange('b c h w -> b (h w) c')),
            ]))
        elif method == 'avg':
            proj = nn.Sequential(OrderedDict([
                ('avg', nn.AvgPool2d(
                    kernel_size=kernel_size,
                    padding=padding,
                    stride=stride,
                    ceil_mode=True
                )),
                ('rearrage', Rearrange('b c h w -> b (h w) c')),
            ]))
        elif method == 'linear':
            proj = None
        else:
            raise ValueError('Unknown method ({})'.format(method))

        return proj

    def forward_conv(self, x, y, h, w):
        if self.with_cls_token:
            cls_token, x = torch.split(x, [1, h*w], 1)

        x = rearrange(x, 'b (h w) c -> b c h w', h=h, w=w)
        y = rearrange(y, 'b (h w) c -> b c h w', h=h, w=w)

        if self.conv_proj_q is not None:
            q = self.conv_proj_q(x)
        else:
            q = rearrange(x, 'b c h w -> b (h w) c')

        if self.conv_proj_k is not None:
            k = self.conv_proj_k(y)
        else:
            k = rearrange(y, 'b c h w -> b (h w) c')

        if self.conv_proj_v is not None:
            v = self.conv_proj_v(y)
        else:
            v = rearrange(y, 'b c h w -> b (h w) c')

        if self.with_cls_token:
            q = torch.cat((cls_token, q), dim=1)
            k = torch.cat((cls_token, k), dim=1)
            v = torch.cat((cls_token, v), dim=1)

        return q, k, v

    def forward(self, x, y, h, w):
        if (
            self.conv_proj_q is not None
            or self.conv_proj_k is not None
            or self.conv_proj_v is not None
        ):
            q, k, v = self.forward_conv(x, y, h, w)

        q = rearrange(self.proj_q(q), 'b t (h d) -> b h t d', h=self.num_heads)
        k = rearrange(self.proj_k(k), 'b t (h d) -> b h t d', h=self.num_heads)
        v = rearrange(self.proj_v(v), 'b t (h d) -> b h t d', h=self.num_heads)

        attn_score = torch.einsum('bhlk,bhtk->bhlt', [q, k]) * self.scale
        attn = F.softmax(attn_score, dim=-1)
        attn = self.attn_drop(attn)

        x = torch.einsum('bhlt,bhtv->bhlv', [attn, v])
        x = rearrange(x, 'b h t d -> b t (h d)')

        x = self.proj(x)
        x = self.proj_drop(x)

        return x
    
class Fusion_Block(nn.Module):
    def __init__(self,
                 dim_in,
                 dim_out,
                 num_heads,
                 mlp_ratio=4.,
                 qkv_bias=False,
                 drop=0.,
                 attn_drop=0.,
                 drop_path=0.,
                 act_layer=nn.GELU,
                 norm_layer=nn.LayerNorm):
        super().__init__()

        self.with_cls_token = False

        self.norm1 = norm_layer(dim_in+dim_out)
        self.attn = Attention_2D(
            dim_in, dim_out, num_heads, qkv_bias, attn_drop, drop,
        )

        self.drop_path = DropPath(drop_path) \
            if drop_path > 0. else nn.Identity()
        self.norm3 = norm_layer(dim_in)

        dim_mlp_hidden = int(dim_out * mlp_ratio)
        self.mlp = Mlp(
            in_features=dim_in,
            hidden_features=dim_mlp_hidden,
            act_layer=act_layer,
            drop=drop
        )

    def forward(self, features, h, w):
        res = features

        x = self.norm1(features)
        attn = self.attn(x, x, h, w)
        x = res + self.drop_path(attn)
        x = x + self.drop_path(self.mlp(self.norm3(x)))

        return x
    
class x13(nn.Module): #
    def __init__(self, config, device):
        super(x13, self).__init__()
        self.config = config
        self.gpu_device = device
        #------------------------------------------------------------------------------------------------
        #CVT
        # # self.pre = AutoImageProcessor.from_pretrained("microsoft/cvt-13")
        # self.cvt = CvtModel.from_pretrained("microsoft/cvt-13")
        # self.avgpool = nn.AvgPool2d(2, stride=2)
        #RGB
        self.rgb_normalizer = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        self.RGB_encoder = models.efficientnet_b3(pretrained=True) #efficientnet_b4
        self.RGB_encoder.classifier = nn.Sequential()
        self.RGB_encoder.avgpool = nn.Sequential()  
        #SS
        self.up = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True) 
        self.conv3_ss_f = ConvBlock(channel=[config.n_fmap_b3[4][-1]+config.n_fmap_b3[3][-1], config.n_fmap_b3[3][-1]])
        self.conv2_ss_f = ConvBlock(channel=[config.n_fmap_b3[3][-1]+config.n_fmap_b3[2][-1], config.n_fmap_b3[2][-1]])
        self.conv1_ss_f = ConvBlock(channel=[config.n_fmap_b3[2][-1]+config.n_fmap_b3[1][-1], config.n_fmap_b3[1][-1]])
        self.conv0_ss_f = ConvBlock(channel=[config.n_fmap_b3[1][-1]+config.n_fmap_b3[0][-1], config.n_fmap_b3[0][0]])
        self.final_ss_f = ConvBlock(channel=[config.n_fmap_b3[0][0], config.n_class], final=True)
        #------------------------------------------------------------------------------------------------
        #red light and stop sign predictor
        self.tls_predictor = nn.Sequential( 
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(config.n_fmap_b3[4][-1], 1),
            nn.ReLU()
        )
        self.tls_biasing = nn.Linear(1, config.n_fmap_b3[4][0])
        self.tls_biasing_bypass = nn.Sequential( 
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(config.n_fmap_b3[4][-1], config.n_fmap_b3[4][0]),
            nn.Sigmoid()
        )


        #nn.Linear(config.n_fmap_b3[4][-1], config.n_fmap_b3[4][0])

        #------------------------------------------------------------------------------------------------
        #SDC
        self.cover_area = config.coverage_area
        self.n_class = config.n_class
        self.h, self.w = config.input_resolution[0], config.input_resolution[1]

        fovh = np.rad2deg(2.0 * np.arctan((self.config.img_height / self.config.img_width) * np.tan(0.5 * np.radians(self.config.fov))))
#        self.fx = self.config.img_width / (2 * np.tan(self.config.fov * np.pi / 360))
        fy = self.config.img_height / (2 * np.tan(fovh * np.pi / 360))

        self.fx = 160  # 160 
        self.x_matrix = torch.vstack([torch.arange(-self.w/2, self.w/2)]*self.h) / self.fx
        self.x_matrix = self.x_matrix.to(device)
        #SC
        self.SC_encoder = models.efficientnet_b1(pretrained=False) 
        self.SC_encoder.features[0][0] = nn.Conv2d(config.n_class, config.n_fmap_b1[0][0], kernel_size=3, stride=2, padding=1, bias=False) 
        self.SC_encoder.classifier = nn.Sequential() 
        self.SC_encoder.avgpool = nn.Sequential()
        self.SC_encoder.apply(kaiming_init)
        #------------------------------------------------------------------------------------------------
        #feature fusion
        self.necks_net = nn.Sequential( #inputnya dari 2 bottleneck
            nn.Conv2d(config.n_fmap_b3[4][-1]+config.n_fmap_b1[4][-1], config.n_fmap_b3[4][1], kernel_size=1, stride=1, padding=0),
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(config.n_fmap_b3[4][1], config.n_fmap_b3[4][0])
        )

#        self.attn_neck = nn.Sequential( #inputnya dari 2 bottleneck
#            nn.Conv2d(config.n_fmap_b3[4][-1], config.n_fmap_b3[4][1], kernel_size=1, stride=1, padding=0),
#            nn.AdaptiveAvgPool2d(1),
#            nn.Flatten(),
#            nn.Linear(config.n_fmap_b3[4][1], config.n_fmap_b3[4][0])
#        )

        embed_dim_q = self.config.fusion_embed_dim_q
        embed_dim_kv = self.config.fusion_embed_dim_kv
        depth = self.config.fusion_depth
        num_heads = self.config.fusion_num_heads
        mlp_ratio = self.config.fusion_mlp_ratio
        qkv_bias = self.config.fusion_qkv
        drop_rate = self.config.fusion_drop_rate
        attn_drop_rate = self.config.fusion_attn_drop_rate
        dpr = self.config.fusion_dpr
        act_layer=nn.GELU
        norm_layer =nn.LayerNorm

        #------------------------------------------------------------------------------------------------
        #wp predictor, input size 5 karena concat dari xy, next route xy, dan velocity
        self.gru = nn.GRUCell(input_size=5, hidden_size=config.n_fmap_b3[4][0])
        self.pred_dwp = nn.Linear(config.n_fmap_b3[4][0], 2)
        #PID Controller
        self.turn_controller = PIDController(K_P=config.turn_KP, K_I=config.turn_KI, K_D=config.turn_KD, n=config.turn_n)
        self.speed_controller = PIDController(K_P=config.speed_KP, K_I=config.speed_KI, K_D=config.speed_KD, n=config.speed_n)
        #------------------------------------------------------------------------------------------------
        #controller
        #MLP Controller
        # self.controller = nn.Sequential(
        #     nn.Linear(config.n_fmap_b3[3][0], config.n_fmap_b3[3][0]//2),
        #     nn.Linear(config.n_fmap_b3[3][0]//2, 3),
        #     nn.ReLU()
        # )
        self.controller = nn.Sequential(
            nn.Linear(config.n_fmap_b3[4][0], config.n_fmap_b3[3][-1]),
            nn.Linear(config.n_fmap_b3[3][-1], 3),
            nn.ReLU()
        )

        blocks = []
        for j in range(depth):
            blocks.append(
                Fusion_Block(
                    dim_in=embed_dim_q+embed_dim_kv,
                    dim_out=embed_dim_q+embed_dim_kv,
                    num_heads=num_heads,
                    mlp_ratio=mlp_ratio,
                    qkv_bias=qkv_bias,
                    drop=drop_rate,
                    attn_drop=attn_drop_rate,
                    drop_path=dpr[j],
                    act_layer=act_layer,
                    norm_layer=norm_layer,
                )
            )
        self.blocks = nn.ModuleList(blocks)
        self.input_buffer = {'depth': deque()}

    def forward(self, rgb_f, depth_f, next_route, velo_in, gt_ss,gt_redl): # 
        #------------------------------------------------------------------------------------------------
        # # CVT and CNN
        # # inputs = self.pre(rgb_f, return_tensors="pt").to(self.gpu_device)
        # # out = self.cvt(**inputs, output_hidden_states=True)
        # embed_dim = [24, 32, 48, 136]
        # in_rgb = self.rgb_normalizer(rgb_f) #[i]
        # out = self.cvt(in_rgb, output_hidden_states=True)
        # RGB_features1 = self.RGB_encoder.features[0](in_rgb)[:,:embed_dim[0],:,:]
        # RGB_features2 = out[2][0][:,:embed_dim[1],:,:]
        # RGB_features3 = out[2][1][:,:embed_dim[2],:,:]
        # RGB_features5 = out[2][2][:,:embed_dim[3],:,:]
        # RGB_features9 = self.RGB_encoder.features[8](out[2][2])
        # RGB_features8 = self.avgpool(RGB_features9)
        # ss_f_3 = self.conv3_ss_f(cat([RGB_features9, RGB_features5], dim=1))

        # # only CNN
        in_rgb = self.rgb_normalizer(rgb_f) #[i]
        RGB_features0 = self.RGB_encoder.features[0](in_rgb)
        RGB_features1 = self.RGB_encoder.features[1](RGB_features0)
        RGB_features2 = self.RGB_encoder.features[2](RGB_features1)
        RGB_features3 = self.RGB_encoder.features[3](RGB_features2)
        RGB_features4 = self.RGB_encoder.features[4](RGB_features3)
        RGB_features5 = self.RGB_encoder.features[5](RGB_features4)
        RGB_features6 = self.RGB_encoder.features[6](RGB_features5)
        RGB_features7 = self.RGB_encoder.features[7](RGB_features6)
        RGB_features8 = self.RGB_encoder.features[8](RGB_features7)
       
        # bagian upsampling
        ss_f_3 = self.conv3_ss_f(cat([self.up(RGB_features8), RGB_features5], dim=1))
        ss_f_2 = self.conv2_ss_f(cat([self.up(ss_f_3), RGB_features3], dim=1))
        ss_f_1 = self.conv1_ss_f(cat([self.up(ss_f_2), RGB_features2], dim=1))
        ss_f_0 = self.conv0_ss_f(cat([self.up(ss_f_1), RGB_features1], dim=1))
        ss_f = self.final_ss_f(self.up(ss_f_0))
        bs,ly,wi,hi = ss_f.shape

        #------------------------------------------------------------------------------------------------
        #create a semantic cloud
        if False: #self.show:
            big_top_view = torch.zeros((bs,ly,2*wi,2*hi)).cuda()
            for i in range(3):
                if i==0:
                    width = 224 # 224
                    depth_f_p = depth_f[:,:,:,:width]
                    ss_f_p = gt_ss[:,:,:,:width]
                    rot = 130 #60 # 43.3
                    height_coverage = 120
                    width_coverage = 300
                elif i==1:
                    width = 224 # 224
                    depth_f_p = depth_f[:,:,:,-width:]
                    ss_f_p = gt_ss[:,:,:,-width:]
                    rot = -65 #-60 # -43.3
                    height_coverage = 120
                    width_coverage = 300
                elif i==2:
                    width = 320 # 320
                    depth_f_p = depth_f[:,:,:,224:768-224]
                    ss_f_p = gt_ss[:,:,:,224:768-224]
                    rot = 0
                    height_coverage = 160
                    width_coverage = 320

                big_top_view = self.gen_top_view_sc_show(big_top_view, depth_f_p, ss_f_p, rot, width, hi,height_coverage,width_coverage) #  ss_f  ,rgb_f

            big_top_view = big_top_view[:,:,0:wi,768-160:768+160]
            self.save2(gt_ss,big_top_view)
        
        big_top_view = torch.zeros((bs,ly,2*wi,hi)).cuda()
        for i in range(3):
            if i==0:
                width = 224 # 224
                rot = 130 #60 # 43.3
                height_coverage = 120
                width_coverage = 300
                big_top_view = self.gen_top_view_sc(big_top_view, depth_f[:,:,:,:width], ss_f[:,:,:,:width], rot, width, hi, height_coverage,width_coverage)
            elif i==1:
                width = 224 # 224
                rot = -65 #-60 # -43.3
                height_coverage = 120
                width_coverage = 300
                big_top_view = self.gen_top_view_sc(big_top_view, depth_f[:,:,:,-width:], ss_f[:,:,:,-width:], rot, width, hi, height_coverage,width_coverage)
            elif i==2:
                width = 320 # 320
                rot = 0
                height_coverage = 160
                width_coverage = 320
                big_top_view = self.gen_top_view_sc(big_top_view, depth_f[:,:,:,224:hi-224], ss_f[:,:,:,224:hi-224], rot, width, hi,height_coverage,width_coverage)

#        top_view_sc = big_top_view[:,:,wi:2*wi,768-160:768+160]
        top_view_sc = big_top_view[:,:,:wi,:]

        #downsampling section
        SC_features0 = self.SC_encoder.features[0](top_view_sc)
        SC_features1 = self.SC_encoder.features[1](SC_features0)
        SC_features2 = self.SC_encoder.features[2](SC_features1)
        SC_features3 = self.SC_encoder.features[3](SC_features2)
        SC_features4 = self.SC_encoder.features[4](SC_features3)
        SC_features5 = self.SC_encoder.features[5](SC_features4)
        SC_features6 = self.SC_encoder.features[6](SC_features5)
        SC_features7 = self.SC_encoder.features[7](SC_features6)
        SC_features8 = self.SC_encoder.features[8](SC_features7)

        #------------------------------------------------------------------------------------------------
        #red light and stop sign detection
        redl_stops = self.tls_predictor(RGB_features8)

        red_light = redl_stops[:,0] #gt_redl
#        tls_bias = self.tls_biasing(redl_stops) #gt_redl.unsqueeze(1))
        tls_bias = self.tls_biasing_bypass(RGB_features8)  + self.tls_biasing(redl_stops) #redl_stops) #gt_redl.unsqueeze(1))

        #------------------------------------------------------------------------------------------------
        #waypoint prediction
        #get hidden state dari gabungan kedua bottleneck

        input = cat([RGB_features8, SC_features8], dim=1)
        hx = self.necks_net(input) #RGB_features_sum+SC_features8 cat([RGB_features_sum, SC_features8], dim=1)

#        RGB_features8 = rearrange(RGB_features8 , 'b c h w-> b (h w) c')
#        SC_features8 = rearrange(SC_features8 , 'b c h w-> b (h w) c')

#        for i, blk in enumerate(self.blocks):
#            x = blk(features_cat, H, W)

#        x = rearrange(x , 'b (h w) c-> b c h w', h=H,w=W)
#        hx = self.attn_neck(x)

        xy = torch.zeros(size=(hx.shape[0], 2)).float().to(self.gpu_device)
        # predict delta wp
        out_wp = list()
        for _ in range(self.config.pred_len):
            ins = torch.cat([xy, next_route, torch.reshape(velo_in, (velo_in.shape[0], 1))], dim=1)
            hx = self.gru(ins, hx)
            d_xy = self.pred_dwp(hx+tls_bias)
            xy = xy + d_xy
            out_wp.append(xy)
        pred_wp = torch.stack(out_wp, dim=1)
        #------------------------------------------------------------------------------------------------
        #control decoder
        control_pred = self.controller(hx+tls_bias) 
        steer = control_pred[:,0] * 2 - 1. # convert from [0,1] to [-1,1]
        throttle = control_pred[:,1] * self.config.max_throttle
        brake = control_pred[:,2] #brake: hard 1.0 or no 0.0

        return ss_f, pred_wp, steer, throttle, brake, red_light,top_view_sc # redl_stops[:,0] , top_view_sc   

    def scale_and_crop_image_cv(self, image, scale=1, crop=256):
        upper_left_yx = [int((image.shape[0]/2) - (crop[0]/2)), int((image.shape[1]/2) - (crop[1]/2))]
        cropped_im = image[upper_left_yx[0]:upper_left_yx[0]+crop[0], upper_left_yx[1]:upper_left_yx[1]+crop[1], :]
        cropped_image = np.transpose(cropped_im, (2,0,1))
        return cropped_image
    
    def rgb_to_depth(self, de_gt):
        de_gt = de_gt.transpose(1, 2, 0)
        arrayd = de_gt.astype(np.float32)
        normalized_depth = np.dot(arrayd, [65536.0, 256.0, 1.0]) # Apply (R + G * 256 + B * 256 * 256) / (256 * 256 * 256 - 1).
        depthx = normalized_depth/16777215.0  # (256.0 * 256.0 * 256.0 - 1.0) --> rangenya 0 - 1
        result = np.expand_dims(depthx, axis=0)
        return result

    def swap_RGB2BGR(self,matrix):
        red = matrix[:,:,0].copy()
        blue = matrix[:,:,2].copy()
        matrix[:,:,0] = blue
        matrix[:,:,2] = red
        return matrix

    def get_wp_nxr_frame(self):
        frame_dim = self.config.crop - 1 #array mulai dari 0
        area = self.config.coverage_area

        point_xy = []
	    #proses wp
        for i in range(1, self.config.pred_len+1):
            x_point = int((frame_dim/2) + (self.control_metadata['wp_'+str(i)][0]*(frame_dim/2)/area[1]))
            y_point = int(frame_dim - (self.control_metadata['wp_'+str(i)][1]*frame_dim/area[0]))
            xy_arr = np.clip(np.array([x_point, y_point]), 0, frame_dim) #constrain
            point_xy.append(xy_arr)
	
	    #proses juga untuk next route
	    # - + y point kebalikan dari WP, karena asumsinya agent mendekati next route point, dari negatif menuju 0
        x_point = int((frame_dim/2) + (self.control_metadata['next_point'][0]*(frame_dim/2)/area[1]))
        y_point = int(frame_dim + (self.control_metadata['next_point'][1]*frame_dim/area[0]))
        xy_arr = np.clip(np.array([x_point, y_point]), 0, frame_dim) #constrain
        point_xy.append(xy_arr)
        return point_xy
		
    def save2(self, ss, sc):
        frame = 0
        ss = ss.cpu().detach().numpy()
        sc = sc.cpu().detach().numpy()

        #buat array untuk nyimpan out gambar
        imgx = np.zeros((ss.shape[2], ss.shape[3], 3))
        imgx2 = np.zeros((sc.shape[2], sc.shape[3], 3))
        #ambil tensor output segmentationnya
        pred_seg = ss[0]
        pred_sc = sc[0]
        inx = np.argmax(pred_seg, axis=0)
        inx2 = np.argmax(pred_sc, axis=0)
        for cmap in self.config.SEG_CLASSES['colors']:
            cmap_id = self.config.SEG_CLASSES['colors'].index(cmap)
            imgx[np.where(inx == cmap_id)] = cmap
            imgx2[np.where(inx2 == cmap_id)] = cmap
	# Image.fromarray(imgx).save(self.save_path / 'segmentation' / ('%06d.png' % frame))
	# Image.fromarray(imgx2).save(self.save_path / 'semantic_cloud' / ('%06d.png' % frame))
	
	#GANTI ORDER BGR KE RGB, SWAP!
        imgx = self.swap_RGB2BGR(imgx)
        imgx2 = self.swap_RGB2BGR(imgx2)

        cv2.imwrite('/home/mohammad/Mohammad_ws/autonomous_driving/e2etransfuser/train_1%06d.png' % frame, imgx) #cetak predicted segmentation
        cv2.imwrite('/home/mohammad/Mohammad_ws/autonomous_driving/e2etransfuser/train_2%06d.png' % frame, imgx2) #cetak predicted segmentation

    def gen_top_view_sc_show_main(self, depth, semseg):
        #proses awal
        depth_in = depth * 1000.0 #normalisasi ke 1 - 1000
        _, label_img = torch.max(semseg, dim=1) #pada axis C
        cloud_data_n = torch.ravel(torch.tensor([[n for _ in range(self.h*self.w)] for n in range(depth.shape[0])])).to(self.gpu_device)

        #normalize ke frame 
        cloud_data_x = torch.round(((depth_in * self.x_matrix) + (self.cover_area[1]/2)) * (self.w-1) / self.cover_area[1]).ravel()
        cloud_data_z = torch.round((depth_in * -(self.h-1) / self.cover_area[0]) + (self.h-1)).ravel()

        #cari index interest
        bool_xz = torch.logical_and(torch.logical_and(cloud_data_x <= self.w-1, cloud_data_x >= 0), torch.logical_and(cloud_data_z <= self.h-1, cloud_data_z >= 0))
        idx_xz = bool_xz.nonzero().squeeze() #hilangkan axis dengan size=1, sehingga tidak perlu nambahkan ".item()" nantinya

        #stack n x z cls dan plot
        coorx = torch.stack([cloud_data_n, label_img.ravel(), cloud_data_z, cloud_data_x])
        coor_clsn = torch.unique(coorx[:, idx_xz], dim=1).long() #tensor harus long supaya bisa digunakan sebagai index
        top_view_sc = torch.zeros_like(semseg) #ini lebih cepat karena secara otomatis size, tipe data, dan device sama dengan yang dimiliki inputnya (semseg)
        top_view_sc[coor_clsn[0], coor_clsn[1], coor_clsn[2], coor_clsn[3]] = 1.0 #format axis dari NCHW
        bs,lay, w, hi = top_view_sc.shape
#        top_view_sc[:,:,:,0:224] = torch.zeros((bs,lay,w,224))
        self.save2(semseg,top_view_sc)

        return top_view_sc

    def gen_top_view_sc_show(self, big_top_view, depth, semseg, rot, im_width, im_height,height_coverage,width_coverage):
        #proses awal
        self.x_matrix2 = torch.vstack([torch.arange(-im_width//2, im_width//2)]*self.h) / self.fx
        self.x_matrix2 = self.x_matrix2.to('cuda')

        depth_in = depth * 1000.0 #normalisasi ke 1 - 1000
        _, label_img = torch.max(semseg, dim=1) #pada axis C
        cloud_data_n = torch.ravel(torch.tensor([[n for _ in range(self.h*im_width)] for n in range(depth.shape[0])])).to(self.gpu_device)
        coverage_area = [64/256*height_coverage,64/256*width_coverage] 

        #normalize to frames
        cloud_data_x = torch.round(((depth_in * self.x_matrix2) + (coverage_area[1]/2)) * (im_width-1) / coverage_area[1]).ravel()
        cloud_data_z = torch.round((depth_in * -(self.h-1) / coverage_area[0]) + (self.h-1)).ravel()

        #look for index interests
        bool_xz = torch.logical_and(torch.logical_and(cloud_data_x <= im_width-1, cloud_data_x >= 0), torch.logical_and(cloud_data_z <= self.h-1, cloud_data_z >= 0))
        idx_xz = bool_xz.nonzero().squeeze() #hilangkan axis dengan size=1, sehingga tidak perlu nambahkan ".item()" nantinya

        #stack n x z cls and plot
        coorx = torch.stack([cloud_data_n, label_img.ravel(), cloud_data_z, cloud_data_x])
        coor_clsn = torch.unique(coorx[:, idx_xz], dim=1).long() #tensor harus long supaya bisa digunakan sebagai index
        top_view_sc = torch.zeros_like(semseg) #ini lebih cepat karena secara otomatis size, tipe data, dan device sama dengan yang dimiliki inputnya (semseg)
        top_view_sc[coor_clsn[0], coor_clsn[1], coor_clsn[2], coor_clsn[3]] = 1.0 #format axis dari NCHW

        bs, ly, wi, hi = top_view_sc.shape
        big_top_view[:,:,0:1*wi,im_height-hi//2:im_height+hi//2] = torch.where(top_view_sc != 0, top_view_sc, big_top_view[:,:,0:1*wi,im_height-hi//2:im_height+hi//2])
        if rot != 0:
            big_top_view = rotate(big_top_view,rot)

        self.save2(semseg,big_top_view)

        return big_top_view
    
    def gen_top_view_sc(self, big_top_view, depth, semseg, rot, im_width, im_height, height_coverage, width_coverage):
        #proses awal
        self.x_matrix2 = torch.vstack([torch.arange(-im_width//2, im_width//2)]*self.h) / self.fx
        self.x_matrix2 = self.x_matrix2.to('cuda')

        depth_in = depth * 1000.0 #normalisasi ke 1 - 1000
        _, label_img = torch.max(semseg, dim=1) #pada axis C
        cloud_data_n = torch.ravel(torch.tensor([[n for _ in range(self.h*im_width)] for n in range(depth.shape[0])])).to(self.gpu_device)
        coverage_area = [64/256*height_coverage,64/256*width_coverage] 

        #normalize to frames
        cloud_data_x = torch.round(((depth_in * self.x_matrix2) + (coverage_area[1]/2)) * (im_width-1) / coverage_area[1]).ravel()
        cloud_data_z = torch.round((depth_in * -(self.h-1) / coverage_area[0]) + (self.h-1)).ravel()

        #look for index interests
        bool_xz = torch.logical_and(torch.logical_and(cloud_data_x <= im_width-1, cloud_data_x >= 0), torch.logical_and(cloud_data_z <= self.h-1, cloud_data_z >= 0))
        idx_xz = bool_xz.nonzero().squeeze() #hilangkan axis dengan size=1, sehingga tidak perlu nambahkan ".item()" nantinya

        #stack n x z cls and plot
        coorx = torch.stack([cloud_data_n, label_img.ravel(), cloud_data_z, cloud_data_x])
        coor_clsn = torch.unique(coorx[:, idx_xz], dim=1).long() #tensor harus long supaya bisa digunakan sebagai index
        top_view_sc = torch.zeros_like(semseg) #ini lebih cepat karena secara otomatis size, tipe data, dan device sama dengan yang dimiliki inputnya (semseg)
        top_view_sc[coor_clsn[0], coor_clsn[1], coor_clsn[2], coor_clsn[3]] = 1.0 #format axis dari NCHW

        bs, ly, wi, hi = top_view_sc.shape
        big_top_view[:,:,0:1*wi,(im_height-hi)//2:(im_height+hi)//2] = torch.where(top_view_sc != 0, top_view_sc, big_top_view[:,:,0:1*wi,(im_height-hi)//2:(im_height+hi)//2])
        if rot != 0:
            big_top_view = rotate(big_top_view,rot)

        return big_top_view
  
    def gen_top_view_sc_main(self, depth, semseg): #gt_seg, rgb_f
        #proses awal
        depth_in = depth * 1000.0 #normalize to 1 - 1000
        _, label_img = torch.max(semseg, dim=1) #pada axis C
        cloud_data_n = torch.ravel(torch.tensor([[n for _ in range(self.h*self.w)] for n in range(depth.shape[0])])).to(self.gpu_device)

        #normalize to frame
        cloud_data_x = torch.round(((depth_in * self.x_matrix) + (self.cover_area[1]/2)) * (self.w-1) / self.cover_area[1]).ravel()
        cloud_data_z = torch.round((depth_in * -(self.h-1) / self.cover_area[0]) + (self.h-1)).ravel()

        #find the interest index
        bool_xz = torch.logical_and(torch.logical_and(cloud_data_x <= self.w-1, cloud_data_x >= 0), torch.logical_and(cloud_data_z <= self.h-1, cloud_data_z >= 0))
        idx_xz = bool_xz.nonzero().squeeze() #remove axis with size=1, so no need to add ".item()" later

        #stack n x z cls dan plot
        coorx = torch.stack([cloud_data_n, label_img.ravel(), cloud_data_z, cloud_data_x])
        coor_clsn = torch.unique(coorx[:, idx_xz], dim=1).long() #tensor must be long so that it can be used as an index

        top_view_sc = torch.zeros_like(semseg) #this is faster because automatically the size, data type, and device are the same as those of the input (semseg)
        top_view_sc[coor_clsn[0], coor_clsn[1], coor_clsn[2], coor_clsn[3]] = 1.0 #axis format from NCHW

        return top_view_sc

    def mlp_pid_control(self, waypoints, velocity, mlp_steer, mlp_throttle, mlp_brake, redl, ctrl_opt="one_of"):
        assert(waypoints.size(0)==1)
        waypoints = waypoints[0].data.cpu().numpy()
        red_light = True if redl.data.cpu().numpy() > 0.5 else False

        waypoints[:,1] *= -1
        speed = velocity[0].data.cpu().numpy()

        aim = (waypoints[1] + waypoints[0]) / 2.0
        angle = np.degrees(np.pi / 2 - np.arctan2(aim[1], aim[0])) / 90
        pid_steer = self.turn_controller.step(angle)
        pid_steer = np.clip(pid_steer, -1.0, 1.0)

        desired_speed = np.linalg.norm(waypoints[0] - waypoints[1]) * 2.0
        delta = np.clip(desired_speed - speed, 0.0, self.config.clip_delta)
        pid_throttle = self.speed_controller.step(delta)
        pid_throttle = np.clip(pid_throttle, 0.0, self.config.max_throttle)
        pid_brake = 0.0

        #final decision
        if ctrl_opt == "one_of":
            #opsi 1: jika salah satu controller aktif, maka vehicle jalan. vehicle berhenti jika kedua controller non aktif
            steer = np.clip(self.config.cw_pid[0]*pid_steer + self.config.cw_mlp[0]*mlp_steer, -1.0, 1.0)
            throttle = np.clip(self.config.cw_pid[1]*pid_throttle + self.config.cw_mlp[1]*mlp_throttle, 0.0, self.config.max_throttle)
            brake = 0.0
            if (pid_throttle >= self.config.min_act_thrt) and (mlp_throttle < self.config.min_act_thrt):
                steer = pid_steer
                throttle = pid_throttle
            elif (pid_throttle < self.config.min_act_thrt) and (mlp_throttle >= self.config.min_act_thrt):
                pid_brake = 1.0
                steer = mlp_steer
                throttle = mlp_throttle
            elif (pid_throttle < self.config.min_act_thrt) and (mlp_throttle < self.config.min_act_thrt):
                # steer = 0.0 #dinetralkan
                throttle = 0.0
                pid_brake = 1.0
                brake = np.clip(self.config.cw_pid[2]*pid_brake + self.config.cw_mlp[2]*mlp_brake, 0.0, 1.0)
        elif ctrl_opt == "both_must":
            #opsi 2: vehicle jalan jika dan hanya jika kedua controller aktif. jika salah satu saja non aktif, maka vehicle berhenti
            steer = np.clip(self.config.cw_pid[0]*pid_steer + self.config.cw_mlp[0]*mlp_steer, -1.0, 1.0)
            throttle = np.clip(self.config.cw_pid[1]*pid_throttle + self.config.cw_mlp[1]*mlp_throttle, 0.0, self.config.max_throttle)
            brake = 0.0
            if (pid_throttle < self.config.min_act_thrt) or (mlp_throttle < self.config.min_act_thrt):
                # steer = 0.0 #dinetralkan
                throttle = 0.0
                pid_brake = 1.0
                brake = np.clip(self.config.cw_pid[2]*pid_brake + self.config.cw_mlp[2]*mlp_brake, 0.0, 1.0)
        elif ctrl_opt == "pid_only":
            #opsi 3: PID only
            steer = pid_steer
            throttle = pid_throttle
            brake = 0.0
            #MLP full off
            mlp_steer = 0.0
            mlp_throttle = 0.0
            mlp_brake = 0.0
            if pid_throttle < self.config.min_act_thrt:
                # steer = 0.0 #dinetralkan
                throttle = 0.0
                pid_brake = 1.0
                brake = pid_brake
        elif ctrl_opt == "mlp_only":
            #opsi 4: MLP only
            steer = mlp_steer
            throttle = mlp_throttle
            brake = 0.0
            #PID full off
            pid_steer = 0.0
            pid_throttle = 0.0
            pid_brake = 0.0
            if mlp_throttle < self.config.min_act_thrt:
                # steer = 0.0 #dinetralkan
                throttle = 0.0
                brake = mlp_brake
        else:
            sys.exit("ERROR, FALSE CONTROL OPTION")

        metadata = {
            'control_option': ctrl_opt,
            'speed': float(speed.astype(np.float64)),
            'steer': float(steer),
            'throttle': float(throttle),
            'brake': float(brake),
            'red_light': float(red_light),
            'cw_pid': [float(self.config.cw_pid[0]), float(self.config.cw_pid[1]), float(self.config.cw_pid[2])],
            'pid_steer': float(pid_steer),
            'pid_throttle': float(pid_throttle),
            'pid_brake': float(pid_brake),
            'cw_mlp': [float(self.config.cw_mlp[0]), float(self.config.cw_mlp[1]), float(self.config.cw_mlp[2])],
            'mlp_steer': float(mlp_steer),
            'mlp_throttle': float(mlp_throttle),
            'mlp_brake': float(mlp_brake),
            'wp_3': tuple(waypoints[2].astype(np.float64)), 
            'wp_2': tuple(waypoints[1].astype(np.float64)),
            'wp_1': tuple(waypoints[0].astype(np.float64)),
            'desired_speed': float(desired_speed.astype(np.float64)),
            'angle': float(angle.astype(np.float64)),
            'aim': tuple(aim.astype(np.float64)),
            'delta': float(delta.astype(np.float64)),
            'car_pos': None, #akan direplace di fungsi agent
            'next_point': None, #akan direplace di fungsi agent
        }
        return steer, throttle, brake, metadata


