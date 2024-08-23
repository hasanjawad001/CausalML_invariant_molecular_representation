## 10 trials, 
## 6 envs,
## 3 models (regular, PCA, Siamese)
## tasks:
##     1. seperate feature target for 6 envs (6 files in v11/datasets/)
##     2. use utils for 2a and 2b- 
##     2a. take the SMILES notation and convert it by "Chemical_SMILES_to_Flat_Fingerprint_RingStructs.ipynb -> convertNewSmilesToOldSmiles" 
##        there are two cells which has the "convertNewSmilesToOldSmiles" function, use the first one (do not use the later cell's function)
##     2b. after converting use "Chemical_SMILES_to_Fingerprint.ipynb -> readSmilesToFingerprints" function to get the fingerprints
##     3. use 10 trials, 6 envs, 3 models to evaluate the performance
## extra:
##     1. estimator PCA from version 1.0.2 (saved object) when using version 0.24.2 (here)z;
##        1a. import nltk, sklearn
##        1b. !pip install -U scikit-learn==1.0.2
##        1c. print('The nltk version is {}.'.format(nltk.__version__))
##        1d. print('The scikit-learn version is {}.'.format(sklearn.__version__))
## extra:
##     1. train with 768 pretrained model (abstract len emb depends on PCA(768) on 90%)
##     *2. use all validation model (with struct_code 0 (simple) and 1 (complex))
##     *3. use v8 (0- simple/PCA, solo, 768) struct
##     *4. use v7 (1- complex, solo, 768) struct
##     5. use v9 (simp+comp, solo env, 768)
##     6. use v10 (simp+comp, solo env, 24)
##     7. use v11 (simp+comp, beef env, 768)

## imports

import numpy as np
from numpy import genfromtxt
import pandas as pd
from transformers import AutoTokenizer, AutoModel, pipeline, AutoModelForMaskedLM
import torch
import torch.nn as nn
from torch.autograd import grad
from torch.utils.data import DataLoader    
import torchvision.utils
from sklearn.model_selection import train_test_split
from sklearn.linear_model import Ridge, RidgeCV, LassoCV
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error
from sklearn.neural_network import MLPRegressor
from sklearn.datasets import make_regression
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from scipy import stats
import random
# from pytorch_lightning.core.lightning import LightningModule
# from pytorch_lightning import Trainer
# import pytorch_lightning as pl
# from ray_lightning import RayPlugin, RayShardedPlugin
import ray
import matplotlib.pyplot as plt
from PIL import Image
import PIL.ImageOps
import os
import re
import requests
from tqdm.auto import tqdm
import pickle as pk
import time
import utils
from sklearn.decomposition import PCA
import math
from pprint import pprint
import warnings
warnings.filterwarnings('ignore')


import nltk, sklearn
print('The nltk version is {}.'.format(nltk.__version__))
print('The scikit-learn version is {}.'.format(sklearn.__version__))

## classes
class SiameseNetwork(torch.nn.Module):
    def __init__(self, len_embedding, abstract_len_embedding, use_irm=False, n_hidden_node=32, struct_code=0):
        '''
            struct_code {0=simple, 1=comple} structure
        '''
        
        super(SiameseNetwork, self).__init__()
        self.loss = nn.L1Loss(reduction="mean") 
        self.use_irm = use_irm
        self.len_embedding = len_embedding
        self.abstract_len_embedding = abstract_len_embedding  
        self.n_hidden_node = n_hidden_node
        #-----------change_1
        if struct_code == 0:
            self.nn_reg = nn.Sequential(
                nn.Linear(self.len_embedding, self.abstract_len_embedding),
            )
        elif struct_code == 1:
            self.nn_reg = nn.Sequential(
                nn.Linear(self.len_embedding, self.n_hidden_node),nn.ReLU(inplace=True),nn.BatchNorm1d(self.n_hidden_node),            
                nn.Linear(self.n_hidden_node, int(self.n_hidden_node/4)),nn.ReLU(inplace=True),nn.BatchNorm1d(int(self.n_hidden_node/4)),nn.Dropout(p=0.2),
                nn.Linear(int(self.n_hidden_node/4), self.abstract_len_embedding),
            )
        else:
            self.nn_reg = nn.Sequential(
                nn.Linear(self.len_embedding, self.abstract_len_embedding),
            )            
        #-----------change_1
        self.nn_final_reg = nn.Sequential(
            nn.Linear(self.abstract_len_embedding * 2, self.n_hidden_node),nn.ReLU(inplace=True),nn.BatchNorm1d(self.n_hidden_node),
            nn.Linear(self.n_hidden_node, int(self.n_hidden_node/4)),nn.ReLU(inplace=True),nn.BatchNorm1d(int(self.n_hidden_node/4)),nn.Dropout(p=0.2),
            nn.Linear(int(self.n_hidden_node/4), 1),
        )

    def forward_reg(self, x):
        output = self.nn_reg(x)
        return output

    def forward_final_reg(self, x):
        output = self.nn_final_reg(x)
        return output

    def forward(self, fp1, fp2):
        a = self.forward_reg(fp1)
        b = self.forward_reg(fp2)
        x = torch.cat([a, b], dim=1)  # hstack
        output = self.forward_final_reg(x)
        return output

    def compute_penalty(self, losses, dummy_w):
        g = grad(losses, dummy_w, create_graph=True)[0]
        r = g.pow(2)
        return r     

## functions

def get_secondary_env(env=None):
    x_e, y_e = env[0].numpy(), env[1].numpy()

    list_primary_feature, list_primary_target = [], []
    list_secondary_feature, list_secondary_target = [], []

    for i in range(x_e.shape[0]):
        list_primary_feature.append(x_e[i])
        list_primary_target.append(y_e[i])
        i += 1

    for i in range(len(list_primary_feature)):
        for j in range(len(list_primary_feature)):
            if i == j:
                pass
            else:
                a = list_primary_feature[i]
                b = list_primary_feature[j]
                sf = np.hstack((a, b))
                st = list_primary_target[i] - list_primary_target[j]
                list_secondary_feature.append(sf)
                list_secondary_target.append(st)
    array_secondary_feature = np.array(list_secondary_feature, dtype='float32')
    array_secondary_target = np.array(list_secondary_target, dtype='float32').reshape((-1, 1))
    senv = torch.from_numpy(array_secondary_feature), torch.from_numpy(array_secondary_target)
    return senv

dir_datasets = '/curdir/datasets/' ## 'datasets/v2/'

df_1_energy = pd.read_excel(dir_datasets + 'file_1.xlsx', sheet_name=0) ## energy sheet
m_1_energy = df_1_energy.values[1:]
m_1_name = m_1_energy[:,[0]]
m_1_smiles = m_1_energy[:,[1]]
m_1_energy_envs = m_1_energy[:,[2, 3, 4, 5]]
for env_no in range(m_1_energy_envs.shape[1]):
    m_1_name_smiles = np.hstack((m_1_name, m_1_smiles))
    feature_target = np.hstack((m_1_name_smiles, m_1_energy_envs[:, [env_no]]))
    np.savetxt(dir_datasets + 'smiles_vs_energy_env_' + str(env_no) + '.csv', feature_target, delimiter = ",", fmt='%s')



## funtion to generate pretrained embedding from SMILE notations

## change_2 : change in v10(24) from v9(768) to accomodate manual 24 length embedding 
def get_embed_from_smiles(list_species_smiles, emb_style='auto_768'):
    if emb_style=='manual_24':
        list_emb = []
        for i, s1 in enumerate(smiles):
            try:
                s2 = utils.convertNewSmilesToOldSmiles(s1)
                s3 = utils.readSmilesToFingerprints(s2)
            except Exception as e:
                print(env_no, i, e)
                s3 = ['-'] * 24
            list_emb.append(s3)    
        return np.array(list_emb)
    else:
        ## --------------- this is commented out in the server ----------------------- ##
        tokenizer = AutoTokenizer.from_pretrained("mrm8488/chEMBL26_smiles_v2")
        model = AutoModel.from_pretrained("mrm8488/chEMBL26_smiles_v2")
        # Load the model into the GPU if avilabile and switch to inference mode
        fe = pipeline('feature-extraction', model=model, tokenizer=tokenizer,device=0)
        sequences_Example = list_species_smiles
        embedding_1 = fe(sequences_Example)
        print(len(embedding_1), len(embedding_1[0]), len(embedding_1[0][0])) 
        n1, n2, n3 = len(embedding_1), len(embedding_1[0]), len(embedding_1[0][0])
        embedding_2 = np.zeros((n1, n3))
        list_embedding = []
        for i, e1 in enumerate(embedding_1):
            e2 = np.array(e1).mean(axis=0)        
            embedding_2[i, :] = e2
        embedding_2 = np.array(embedding_2)
        print('embedding_2 shape: ', embedding_2.shape)
        return embedding_2

dir_datasets = '/curdir/datasets/'
for env_no in range(4): 
    file_name = dir_datasets + 'smiles_vs_energy_env_' + str(env_no) + '.csv'
    df = pd.read_csv(file_name, sep=",", header=None) 
    m = df.values
    smiles = [row[1] for row in m] ## 1 for smiles ==> (row[0],row[1],row[2]) == (name, smiles, energy)
    
    array_emb = get_embed_from_smiles(smiles, emb_style='auto_768') ## change_2 : change in v10(24) from v9(768) to accomodate manual 24 length embedding     
    feature_target = np.hstack((array_emb, m[:,[2]])) ## index 2 used here for energy
    
    print(feature_target.shape)
    np.savetxt('/curdir/datasets/embedding_vs_energy_env_' + str(env_no) + '.csv', feature_target, delimiter = ",", fmt='%s')
#     print()

## change_2 : change in v10(24) from v9(768) to accomodate manual 24 length embedding 
## skip invalid samples => index in each env [41,41,41,41,'-',9] for manual_24
## skip invalid samples => index in each env ['-','-','-','-','-','-'] for auto_768
list_skip_index = ['-','-','-','-']
dir_datasets = '/curdir/datasets/'
for env_no in range(4): 
    data = genfromtxt(dir_datasets + 'embedding_vs_energy_env_' + str(env_no) + '.csv', delimiter=',', dtype='float32')
    skip_index = list_skip_index[env_no]
    if skip_index == '-':
        feature = data[:, 0:-1]
        target = data[:, [-1]]
    elif type(skip_index)== type(0):
        feature = np.vstack((data[:skip_index, 0:-1], data[skip_index+1:, 0:-1]))
        target = np.vstack((data[:skip_index, [-1]], data[skip_index+1:, [-1]]))
    else:
        print('Error!!!')
    feature_target = np.hstack((feature, target))
    print(feature_target.shape)
    np.savetxt('/curdir/datasets/filtered_embedding_vs_energy_env_' + str(env_no) + '.csv', feature_target, delimiter = ",", fmt='%s')

## reading data for Siamese

dir_datasets = '/curdir/datasets/'
#
df_1_energy = pd.read_excel(dir_datasets + 'file_1.xlsx', sheet_name=0) ## energy sheet
df_1_feature = genfromtxt(dir_datasets + 'embedding_vs_energy_env_0.csv', delimiter=',', dtype='float32')
df_3_energy = pd.read_excel(dir_datasets + 'file_2.xlsx', sheet_name=0, engine='openpyxl') # pt111
df_3_feature = genfromtxt(dir_datasets + 'embedding_vs_energy_env_0.csv', delimiter=',', dtype='float32')
#
m_1_energy = df_1_energy.values[1:]
m_1_feature = df_1_feature[:,list(range(0,768))]
m_3_energy = df_3_energy.values.T
m_3_feature = df_3_feature[:,list(range(0,768))]
#
print(m_1_energy.shape)
print(m_1_feature.shape)
print(m_3_energy.shape)
print(m_3_feature.shape)





ray.shutdown()
ray.init(ignore_reinit_error=True, num_cpus=16) ## detects automatically: num_cpus=64

## functions

def get_best_models(
    num_sample_envs=None, m_1_feature=None, m_1_energy=None, m_3_energy=None, skip_index_beef=None,
    len_embedding=None, abstract_len_embedding=None, num_iterations=None, random_state=None
):
    list_index = list(range(2000))
    if random_state is None:
        random.shuffle(list_index)
    else:
        random.Random(random_state).shuffle(list_index)
    list_sample_index = list_index[0: num_sample_envs]
    
    ## train: target-feature
    data_feature_train = m_1_feature
    data_target_train_core = np.array(m_1_energy[:,[3]], dtype='float32')
    data_target_train = np.zeros((data_target_train_core.shape[0], num_sample_envs))
    for i in range(data_target_train_core.shape[0]):
        for j in range(num_sample_envs):
            sample_index = list_sample_index[j]
            mean = data_target_train_core[i][0]
            deviation = m_3_energy[i][sample_index]
            value = mean + deviation
            data_target_train[i][j] = value
    if skip_index_beef is not None:
        data_feature_train = np.vstack((data_feature_train[:skip_index_beef, :], data_feature_train[skip_index_beef+1:, :]))
        data_target_train = np.vstack((data_target_train[:skip_index_beef, :], data_target_train[skip_index_beef+1:, :]))  
    #     print(data_feature_train.shape, data_target_train.shape)    
    else:
        pass
    
    ## environments: primary
    environments = []
    for i in range(data_target_train.shape[1]):
        env = torch.from_numpy(data_feature_train[0:]), torch.from_numpy(data_target_train[0:, [i]])
        environments.append(env)
#     print(len(environments), environments[0][0].shape, environments[0][1].shape)
    
    # print('==============================================================================================================================')
    # print(len(environments))
    # for i, env in enumerate(environments):
    #     features, targets = env
    #     print(features.shape, targets.shape)
    #     if torch.isnan(features).any():
    #         print(f"Environment {i} contains NaN values.")
    # print('==============================================================================================================================')
    
    
    ## environments: secondary
    senvironments = []
    for env in environments:
        senv = get_secondary_env(env=env)
        senvironments.append(senv)
#     print(len(senvironments), senvironments[0][0].shape, senvironments[0][1].shape)  
    
    ## model siamese
    list_model_info = []
    best_model_info, best_loss_siamese = (None, None, None, None), math.inf
    #-----------change_1    
    for _n_hidden_node in [512]: ## hint: select something that is divisible by 4  
    #-----------change_1
        for _lr in [1e-2]: 
            for _struct_code in [1]:
                model_siamese = SiameseNetwork(
                    len_embedding, abstract_len_embedding, use_irm=False, n_hidden_node=_n_hidden_node,
                    struct_code=_struct_code
                )
                optimizer_siamese = torch.optim.Adam(model_siamese.parameters(), lr=_lr)
                
                loss_siamese = None
                for epoch in range(num_iterations):
                    error_siamese = 0
                    for x, y in senvironments:
                        p = torch.randperm(len(x))
                        x_e = x[p]
                        y_e = y[p]
                        fp1 = x_e[:, list(range(0, len_embedding, 1))]
                        fp2 = x_e[:, list(range(len_embedding, 2 * len_embedding, 1))]
                        y_pred_siamese = model_siamese(fp1, fp2) 
                        error_e_siamese = model_siamese.loss(y_pred_siamese, y_e)
                        error_siamese += error_e_siamese
                        
                    error_siamese = error_siamese/len(senvironments)
                    loss_siamese = 1 * error_siamese 

                    optimizer_siamese.zero_grad() ## clear buffer   
                    loss_siamese.backward() ## calculate gradient for all params
                    optimizer_siamese.step() ## update parameters using calculated gradients
                
                if loss_siamese < best_loss_siamese:
                    best_model_info = (_n_hidden_node, _lr, _struct_code, model_siamese)
                    best_loss_siamese = loss_siamese
                file1 = open('logger.log', 'a+')  
                file1.writelines(
                    'n_hidden_node, lr, struct_code, loss_siamese, best_loss_siamese: ' \
                    + str(_n_hidden_node) + str(_lr) + str(_struct_code) \
                    + str(loss_siamese) + str(best_loss_siamese) + '\n\n'
                )
                file1.close()                
                model_info = (_n_hidden_node, _lr, _struct_code, model_siamese)
                list_model_info.append(model_info)
             
    return best_model_info, list_model_info        

@ray.remote(num_returns=1)
def get_result(
    st, trial_no, list_env, dir_datasets, should_standardize, 
    num_sample_envs, m_1_feature, m_1_energy, m_3_energy, skip_index_beef, 
    len_embedding, num_iterations, pca_n_percent
):
    for env_no in list_env:
        d_result = {}        
        data = genfromtxt(dir_datasets + 'filtered_embedding_vs_energy_env_' + str(env_no) + '.csv', delimiter=',', dtype='float32')
        feature = data[:, 0:-1]
        target = data[:, [-1]]
        if should_standardize:
            scaler = StandardScaler().fit(feature)
            feature = scaler.transform(feature)
            
        #####################################################################################################################################            
        ## model 1: regular
        model_name = 'regular' ## use the embeddings as it is
        feature_regular, target_regular = feature, target
        X_train, X_test, y_train, y_test = train_test_split(
            feature_regular, target_regular, test_size=0.33, random_state=trial_no
        )        
        ##
        trainX, testX, trainY, testY = X_train, X_test, y_train.ravel(), y_test.ravel()
        mlrun = utils.MLPredsByCV(cross_validation_split_no = 5)   
        for alg in ['ridge','lasso','elastic','krr','svr']: ## ['ridge','lasso','elastic','krr','svr','gp']:
            if alg == 'svr':
                errors = mlrun.SVR_CV(trainX, testX, trainY, testY)
            elif alg == 'krr':
                errors = mlrun.KRR_CV(trainX, testX, trainY, testY)
            elif alg == 'ridge':
                errors = mlrun.Ridge_CV(trainX, testX, trainY, testY)
            elif alg == 'lasso':
                errors = mlrun.Lasso_CV(trainX, testX, trainY, testY)
            elif alg == 'elastic':
                errors = mlrun.Elastic_CV(trainX, testX, trainY, testY)
            error = np.mean(errors)
            key = (str(trial_no), str(env_no), str(model_name), str(alg))
            d_result[key] = error
        file1 = open('logger.log', 'a+')  
        file1.writelines(
            'time, trial_no, env_no, model_name: ' \
            + str(time.time()-st) + '=>  ' + str(trial_no) + ', ' \
            + str(env_no) + ', ' + str(model_name) + '\n\n'
        )
        file1.close()            
        ##################################################################################################################################### 
        ## model 2: PCA
        model_name = 'PCA' ## use the transformed embeddings by PCA
        pca_dump_name = dir_datasets + 'pca_' + str(env_no) + '.pkl'
        if should_standardize:
            pca_dump_name = dir_datasets + 'pca_std_' + str(env_no) + '.pkl'                    
        #-----------change_1
        pca = PCA(n_components=pca_n_percent) ## e.g. 24 => 6 or other number of components
        feature_train, feature_test, target_train, target_test = train_test_split(
            feature, target, test_size=0.33, random_state=trial_no
        )    
        pca.fit(feature_train)
        ## pickle dump
        pk.dump(pca, open(pca_dump_name,"wb"))
        ## later reload the pickle file
        time.sleep(trial_no*8)           
        pca = pk.load(open(pca_dump_name,"rb"))
        #-----------change_1
        pca_n_components = pca.n_components_
        file1 = open('logger.log', 'a+')  
        file1.writelines(str(pca_n_components) + '\n\n')
        file1.close()            

        feature_pca, target_pca = pca.transform(feature), target
        X_train, X_test, y_train, y_test = train_test_split(
            feature_pca, target_pca, test_size=0.33, random_state=trial_no
        )    
        ##
        trainX, testX, trainY, testY = X_train, X_test, y_train.ravel(), y_test.ravel()
        mlrun = utils.MLPredsByCV(cross_validation_split_no = 5)                
        for alg in ['ridge','lasso','elastic','krr','svr']: ## ['ridge','lasso','elastic','krr','svr','gp']:
            if alg == 'svr':
                errors = mlrun.SVR_CV(trainX, testX, trainY, testY)
            elif alg == 'krr':
                errors = mlrun.KRR_CV(trainX, testX, trainY, testY)
            elif alg == 'ridge':
                errors = mlrun.Ridge_CV(trainX, testX, trainY, testY)
            elif alg == 'lasso':
                errors = mlrun.Lasso_CV(trainX, testX, trainY, testY)
            elif alg == 'elastic':
                errors = mlrun.Elastic_CV(trainX, testX, trainY, testY)
            error = np.mean(errors)
            key = (str(trial_no), str(env_no), str(model_name), str(alg))
            d_result[key] = error
        file1 = open('logger.log', 'a+')  
        file1.writelines(
            'time, trial_no, env_no, model_name: ' \
            + str(time.time()-st) + '=>  ' + str(trial_no) + ', ' \
            + str(env_no) + ', ' + str(model_name) + '\n\n'
        )
        file1.close()            
            
        #####################################################################################################################################    
        ## model 3: Siamese
        pca_dump_name = dir_datasets + 'pca_' + str(env_no) + '.pkl'
        if should_standardize:
            pca_dump_name = dir_datasets + 'pca_std_' + str(env_no) + '.pkl'   
        time.sleep(trial_no*12)                       
        pca = pk.load(open(pca_dump_name,"rb"))
        pca_n_components = pca.n_components_
        #-----------------------------------------------------------------------------------
#         filename_best_model_info = 'best_model_info_' + str(trial_no)
#         filename_list_model_info = 'list_model_info_' + str(trial_no)        
#         try:
#             with open("datasets/" + filename_best_model_info + ".pickle", 'rb') as handle:
#                 best_model_info = pk.load(handle)
#             with open("datasets/" + filename_list_model_info + ".pickle", 'rb') as handle:
#                 list_model_info = pk.load(handle)
                
#             file1 = open('logger.log', 'a+')  
#             file1.writelines(
#                 'trial_no: ' + str(trial_no) + ', Yes, saved model found!' + '\n\n'
#             )
#             file1.close()                            
#         except Exception as e: 
#             file1 = open('logger.log', 'a+')  
#             file1.writelines(
#                 'trial_no: ' + str(trial_no) + ', No saved model found: '+ str(e) + '\n\n'
#             )
#             file1.close()            
            
#             best_model_info, list_model_info = get_best_models(
#                 num_sample_envs=num_sample_envs, m_1_feature=m_1_feature, m_1_energy=m_1_energy,
#                 m_3_energy=m_3_energy, skip_index_beef=skip_index_beef, len_embedding=len_embedding, abstract_len_embedding=pca_n_components,
#                 num_iterations=num_iterations
#             )
#             with open("datasets/" + filename_best_model_info + ".pickle", 'wb') as handle: 
#                 pk.dump(best_model_info, handle, protocol=pk.HIGHEST_PROTOCOL)
#             with open("datasets/" + filename_list_model_info + ".pickle", 'wb') as handle: 
#                 pk.dump(list_model_info, handle, protocol=pk.HIGHEST_PROTOCOL)
        best_model_info, list_model_info = get_best_models(
            num_sample_envs=num_sample_envs, m_1_feature=m_1_feature, m_1_energy=m_1_energy,
            m_3_energy=m_3_energy, skip_index_beef=skip_index_beef, len_embedding=len_embedding, abstract_len_embedding=pca_n_components,
            num_iterations=num_iterations, random_state=trial_no
        )                            
    
        for model_info in [best_model_info]:            
            _nhn, _lr, _sc, model_siamese = model_info[0], model_info[1], model_info[2], model_info[3]  
            
            # model_name = 'Siamese_' + str(_nhn) + '_' + str(_lr) + '_' + str(_sc) ## use the transformed embeddings by Siamese
            model_name = 'Siamese'            
            feature_siamese, target_siamese = model_siamese.forward_reg(torch.from_numpy(feature)).detach().numpy(), target 
            X_train, X_test, y_train, y_test = train_test_split(
                feature_siamese, target_siamese, test_size=0.33, random_state=trial_no
            )    
        #-----------------------------------------------------------------------------------        
        #
            trainX, testX, trainY, testY = X_train, X_test, y_train.ravel(), y_test.ravel()
            mlrun = utils.MLPredsByCV(cross_validation_split_no = 5)                
            for alg in ['ridge','lasso','elastic','krr','svr']: ## ['ridge','lasso','elastic','krr','svr','gp']:
                if alg == 'svr':
                    errors = mlrun.SVR_CV(trainX, testX, trainY, testY)
                elif alg == 'krr':
                    errors = mlrun.KRR_CV(trainX, testX, trainY, testY)
                elif alg == 'ridge':
                    errors = mlrun.Ridge_CV(trainX, testX, trainY, testY)
                elif alg == 'lasso':
                    errors = mlrun.Lasso_CV(trainX, testX, trainY, testY)
                elif alg == 'elastic':
                    errors = mlrun.Elastic_CV(trainX, testX, trainY, testY)
                error = np.mean(errors)
                key = (str(trial_no), str(env_no), str(model_name), str(alg))
                d_result[key] = error
            file1 = open('logger.log', 'a+')  
            file1.writelines('siamese: time, trial_no, env_no, model_name: ' + str(time.time()-st) + '=>  ' + str(trial_no) + ', ' + str(env_no) + ', ' + str(model_name) + '\n\n')
            file1.close()            

        #####################################################################################################################################
        ## model 4: SiameseIRM
        #####################################################################################################################################     
        print('trial_no, env_no, time: ', trial_no, env_no, time.time()-st)    
        
        with open('/curdir/datasets/d_result_' + str(trial_no) + '_' + str(env_no) + '.pickle', 'wb') as handle: 
            pk.dump(d_result, handle, protocol=pk.HIGHEST_PROTOCOL)
    return 0



if __name__=='__main__':
    num_trials = 10
    list_env = [0] 
    num_iterations = 250
    ##
    num_sample_envs = 50
    dir_datasets = '/curdir/datasets/'
    should_standardize = False
    skip_index_beef = None ## 41
    len_embedding=768 ## change_2
    pca_n_percent=0.90 ## change_2

    ## main_1

    st = time.time()
    list_result_id = []
    for trial_no in range(num_trials):
        result_id = get_result.remote(
            st, trial_no, list_env, dir_datasets, should_standardize, 
            num_sample_envs, m_1_feature, m_1_energy, m_3_energy, skip_index_beef, 
            len_embedding, num_iterations, pca_n_percent
        )
        list_result_id.append(result_id)
    list_result = ray.get(list_result_id)

