#######################This is a Serial code file ############################
import numpy as np # linear algebra
import pandas as pd # data processing, CSV file I/O (e.g. pd.read_csv)
import os
from glob import glob
import random
import re
from copy import deepcopy
from typing import Union, List, Tuple, Optional, Callable
from collections import OrderedDict, defaultdict
import math
import time
import cv2
import torch
import torch.nn as nn
from torch.utils.data import Dataset,DataLoader
from torch.utils.data.sampler import SequentialSampler, RandomSampler
from torchvision import transforms, models
from torchvision.transforms import Normalize
from tqdm import tqdm
from sklearn.cluster import DBSCAN
import time
start_time = time.time()

NN_MODEL_PATHS = [
                  'input/kdold_deep-fake_effb2/fold0-effb2-000epoch.pt',
                  'input/kdold_deep-fake_effb2/fold0-effb2-001epoch.pt',
                  'input/kdold_deep-fake_effb2/fold0-effb2-002epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold0-flip-effb2-000epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold0-flip-effb2-001epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold0-flip-effb2-002epoch.pt',

                  'input/kdold_deep-fake_effb2/fold1-effb2-000epoch.pt',
                  'input/kdold_deep-fake_effb2/fold1-effb2-001epoch.pt',
                  'input/kdold_deep-fake_effb2/fold1-effb2-002epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold1-flip-effb2-000epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold1-flip-effb2-001epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold1-flip-effb2-002epoch.pt',

                  'input/kdold_deep-fake_effb2/fold2-effb2-000epoch.pt',
                  'input/kdold_deep-fake_effb2/fold2-effb2-001epoch.pt',
                  'input/kdold_deep-fake_effb2/fold2-effb2-002epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold2-flip-effb2-000epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold2-flip-effb2-001epoch.pt',
                  'input/kfold-deep-fake-effb2_flip/fold2-flip-effb2-002epoch.pt'

]

TARGET_H, TARGET_W = 224, 224
FRAMES_PER_VIDEO = 30
TEST_VIDEOS_PATH = 'dataset/test_videos'
#print(%%time) 
import glob
videos = []
videos_arr = glob.glob(TEST_VIDEOS_PATH+"/*.mp4")
for video_path in videos_arr: # glob(os.path.join(TEST_VIDEOS_PATH, '*.mp4')):
    videos.append({'filename': video_path.split('/')[-1], 'video_path': video_path})

SEED = 42

def seed_everything(seed):
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = False
    torch.backends.cudnn.benchmark = True

seed_everything(SEED)

import sys
sys.path.insert(0, "input/face_detector")

from face_detector import FaceDetector
from face_detector.utils import VideoReader

from efficientnet_pytorch import EfficientNet

def get_net():
    net = EfficientNet.from_name('efficientnet-b2')
    net._fc = nn.Linear(in_features=net._fc.in_features, out_features=2, bias=True)
    return net

class DatasetRetriever(Dataset):

    def __init__(self, df):
        self.video_paths = df['video_path']
        self.filenames = df.index
        self.face_dr = FaceDetector(frames_per_video=FRAMES_PER_VIDEO)

        mean = [0.485, 0.456, 0.406]
        std = [0.229, 0.224, 0.225]
        self.normalize_transform = Normalize(mean, std)
        
        self.video_reader = VideoReader()
        self.video_read_fn = lambda x: self.video_reader.read_frames(x, num_frames=FRAMES_PER_VIDEO)

    def __len__(self):
        return self.filenames.shape[0]

    def __getitem__(self, idx):
        video_path = self.video_paths[idx]
        filename = self.filenames[idx]
        
        my_frames, my_idxs = self.video_read_fn(video_path)
        faces = self.face_dr.get_faces(
            my_frames, my_idxs,
            0.7, 0.7, 0.7, 0.6
        )

        n = len(faces)

        video = torch.zeros((n, 3, TARGET_H, TARGET_W))
        for i, face in enumerate(faces[:n]):
            face = 255 - face
            face = face.astype(np.float32)/255.
            face = torch.tensor(face)
            face = face.permute(2,0,1)
            face = self.normalize_transform(face)
            video[i] = face

        return filename, video


    
df = pd.DataFrame(videos).set_index('filename')

videos = None
del videos

print(df.head())
#print("--- %s hours ---" % ((time.clock())/120))

###################Deepfake Predictor#######################

class DeepFakePredictor:

    def __init__(self):
        self.models = [self.prepare_model(get_net(), path) for path in NN_MODEL_PATHS]
        self.models_count = len(self.models)

    def predict(self, dataset):
        result = []
        
        with torch.no_grad():
            for filename, video in dataset:
                video = video.to(self.device, dtype=torch.float32)
                try:
                    label = self.predict_ensemble(video)
                except Exception as e:
                    print(f'Warning! {e}, {type(e)}')
                    label = 0.5

                result.append({
                    'filename': filename,
                    'label': label,
                })

        return pd.DataFrame(result).set_index('filename')

    def prepare_model(self, model, path):
        self.device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
        model.to(self.device);

        if torch.cuda.is_available():
            model = model.cuda()
            
        if torch.cuda.is_available():
            checkpoint = torch.load(path)
        else:
            checkpoint = torch.load(path, map_location=torch.device('cpu'))
            
        model.load_state_dict(checkpoint['model_state_dict'])
        model.eval()
        print(f'Model prepared. Device is {self.device}')
        return model
    
    @staticmethod
    def net_forward(net, inputs):
        bs = inputs.size(0)
        # Convolution layers
        x = net.extract_features(inputs)
        # Pooling and final linear layer
        x = net._avg_pooling(x)
        emb = x.view(bs, -1)
        x = net._dropout(emb)
        x = net._fc(x)
        return emb, x
    
    def postprocess(self, embs, predictions):
        clusters = defaultdict(list)
        for prediction, cluster_id in zip(predictions, DBSCAN(eps=1.2, min_samples=1).fit_predict(embs)):
            clusters[cluster_id].append(prediction)
        sorted_clusters = sorted(clusters.items(), key=lambda x: -len(x[1]))
        if len(sorted_clusters) < 2:
            return sorted_clusters[0][1]
        if len(sorted_clusters[1][1]) / len(predictions) > 0.25:
            return sorted_clusters[0][1] + sorted_clusters[1][1]
        return sorted_clusters[0][1]
    
    def predict_ensemble(self, video):
        embs, predictions = 0, 0
        for model in self.models:
            emb, prediction = self.net_forward(model, video)
            predictions += prediction / self.models_count
            embs += emb / self.models_count

        predictions = nn.functional.softmax(predictions, dim=1).data.cpu().numpy()[:,1]
        embs = embs.cpu().numpy()
        
        predictions = self.postprocess(embs, predictions)
        return np.mean(predictions)

#%%time
deep_fake_predictor = DeepFakePredictor()

from concurrent.futures import ThreadPoolExecutor

def process_dfs(df, num_workers=2):
    def process_df(sub_df):
        dataset = DatasetRetriever(sub_df)
        result = deep_fake_predictor.predict(dataset)
        return result

    with ThreadPoolExecutor(max_workers=num_workers) as ex:
        results = ex.map(process_df, np.split(df, num_workers))

    return results


#%%time

import time
#start_time = time.time()


count = df.shape[0]


time_start = time.time()
results = process_dfs(df[:count])
dtime = time.time() - time_start

print(f'[speed]:', round(dtime / count, 2), 'sec/video')
print(f'[sum_time]:', f'~{round(dtime / count * 4000 / 60)}', 'min')

result = pd.concat(list(results))
print(result)
print("--- %s hours ---" % ((time.process_time())/120))
print("--- %s seconds ---" % (time.time() - start_time))
print("--- %s minustes ---" % ((time.time() - start_time)/60))
print("--- %s hours ---" % ((time.time() - start_time)/120))









