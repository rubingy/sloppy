from typing import Optional
import torch
import torch.nn.functional as F
import torch.nn as nn

import numpy as np
import numpy as np
import torch
from torch import nn, optim
from torch.utils.data import DataLoader
import importlib
import copy
import argparse
from torchvision import transforms, datasets
from models.fc import Network
from models.lenet import lenet
from matplotlib import pyplot as plt
import torch.nn.functional as F
from scipy.sparse.linalg import LinearOperator
from scipy.sparse.linalg import eigsh
from torch.autograd import Variable, grad
from numpy.linalg import eig as eig
from torch.distributions.multivariate_normal import MultivariateNormal
from dataset import *
from functions import *
from models.wide_resnet_1 import WideResNet

from models.fc import Network
from utils import *
import time



# reproduction of Karolina's method


parser = argparse.ArgumentParser()
parser.add_argument("--gpu_device", type=str,
                    default='cuda:0',
                    help="gpu device")
arg = parser.parse_args()


#######################
##### dataset #########
#######################




# prepare dataset
device = torch.device(arg.gpu_device if torch.cuda.is_available() else 'cpu')
kwargs = {'num_workers': 1, 'pin_memory': True} if device == 'cuda' else {}


# dataset
num_true = 55000  # number of samples with true labels used in training
num_prior = 5000   # num samples used for prior calculation
num_approx = 10000
num_classes = 10
num_random = 0
dataset = "mnist"
num_inplanes = 1 if dataset == "mnist" else 3

# lenet

model_name = "lenet"
args = ()
path = create_path(model_name, args, num_true, num_random, dataset)
print(path)
mkdir(path)
model = lenet(*args).to(device)



train_set, train_set_prior, train_set_approx, test_set = create_mnist(num_classes, num_true, num_prior, num_approx)

train_loader = torch.utils.data.DataLoader(train_set,
                                          batch_size=500,
                                          shuffle = True,
                                          **kwargs)

train_loader_approx = torch.utils.data.DataLoader(train_set_approx,
                                          batch_size=len(train_set_approx.data),
                                          shuffle = True,
                                          **kwargs)

train_loader_FIM = torch.utils.data.DataLoader(train_set_approx,
                                          batch_size=1,
                                          shuffle = True,
                                          **kwargs)

train_loader_prior = torch.utils.data.DataLoader(train_set_prior,
                                          batch_size=len(train_set_prior.data),
                                          shuffle = True,
                                          **kwargs)

test_loader = torch.utils.data.DataLoader(test_set,
                                         batch_size=500,
                                         shuffle = True,
                                         **kwargs)










##########################
##### Bayes class ########
##########################


class bayesian_nn(nn.Module):

    def __init__(self, c, args, ns=150):
        super().__init__()
        self.w = c(*args).to(device)
        self.mu_std = nn.ModuleList([c(*args).to(device), c(*args).to(device)])
        self.ns, self.args = ns, args
        orig_params_w, names_all_w = get_names_params(self.w)
        self.names_all_w = names_all_w
        self.c = c


    def forward(self, x):

        ys = []
        for _ in range(self.ns):
            for name, m, v in zip(self.names_all_w, list(self.mu_std[0].parameters()), list(self.mu_std[1].parameters())):

                # print(name, m.shape)
                r = torch.randn_like(m).mul(torch.sqrt(torch.exp(2*v)))
                del_attr(self.w, name.split("."))
                set_attr(self.w, name.split("."), r+m)  
                # self.w = c(*args).to(device)


            y = self.w(x)
            ys.append(y)

        self.w = self.c(*args).to(device)
        return torch.stack(ys)







###########################
####### functions #########
###########################


def sec(model, model_init, rho, num_samples, device, b = 100, c = 0.1, delta = 0.025):

    epsilon = torch.exp(2*rho)
    pi = torch.tensor(np.pi)
    kl_1, kl_2 = 0, 0

    test = 0

    for m0, m, xi in zip(
        model_init.parameters(), 
        model.mu_std[0].parameters(), 
        model.mu_std[1].parameters(), 
    ):

        q = torch.exp(2*xi)
        p = epsilon
        kl_1 += (1/p) * torch.sum((m0-m)**2)
        kl_2 += torch.sum(q / p) + torch.sum(torch.log(p / q))
        kl_2 += -m.numel()


    kl = (kl_1 + kl_2) / 2
    penalty = 2*torch.log(2*torch.abs(b*torch.log(c / epsilon))) + torch.log(pi**2*num_samples / 6*delta)
    
    
    sec = torch.sqrt((kl + penalty) / (2*(num_samples - 1)))

    return sec, kl, kl_1, kl_2, penalty



    
    
    
    
    
    
    
    
    
    
    
    
def train(model,model_init, num_samples, device, train_loader, criterion, optimizer, rho, num_classes):

    model.train()
    for (data, targets) in train_loader:
        # print(len(data))

        loss2, kl, kl_1, kl_2, penalty = sec(model, model_init, rho ,num_samples, device)
        

        data, targets = data.to(device), targets.to(device)
        output = model(data)
        output = output.reshape(model.ns * len(data), num_classes)
        targets = targets.repeat(model.ns)
        loss = criterion(output, targets) * (1/np.log(2))

        optimizer.zero_grad()
        (loss2 + loss).backward()

        optimizer.step()

    print("loss2, kl, kl1, kl2, p", loss2.item(), kl.item(), kl_1.item(), kl_2.item(), penalty.item())











def train_LBFGS(model,model_init, num_samples, device, train_loader, criterion, optimizer, rho, num_classes):

    model.train()
    for (data, targets) in train_loader:
        data, targets = data.to(device), targets.to(device)
        targets = targets.repeat(model.ns)
        loss2, kl, kl_1, kl_2, penalty = sec(model, model_init, rho ,num_samples, device)

        def closure():
            loss2, kl, kl_1, kl_2, penalty = sec(model, model_init, rho ,num_samples, device)
            output = model(data)
            output = output.reshape(model.ns * len(data), num_classes)   
            loss1 = criterion(output, targets) * (1/np.log(2))
            loss = loss1 + loss2

            optimizer.zero_grad()
            loss.backward()

            return loss

        optimizer.step(closure)

    print("loss2, kl, kl1, kl2, p", loss2.item(), kl.item(), kl_1.item(), kl_2.item(), penalty.item())













def val(model, device, val_loader, criterion, num_classes):
    
    model.eval()
    sum_loss, sum_corr = 0, 0

    

    for (data, targets) in val_loader:
        data, targets = data.to(device), targets.to(device)
        output = model(data)
        output = output.reshape(model.ns * len(data), num_classes)
        targets = targets.repeat(model.ns)
        loss = criterion(output, targets)
        pred = output.max(1)[1]
        sum_loss += loss.item()
        sum_corr += pred.eq(targets).sum().item() / len(targets)

    err_avg = 1 - (sum_corr/len(val_loader))
    loss_avg = sum_loss / len(val_loader)
    


    return err_avg, loss_avg






def val_d(model, device, val_loader, criterion, num_classes):
    
    model.eval()
    sum_loss, sum_corr = 0, 0

    

    for (data, targets) in val_loader:
        data, targets = data.to(device), targets.to(device)
        output = model(data)
        loss = criterion(output, targets)
        pred = output.max(1)[1]
        sum_loss += loss.item()
        sum_corr += pred.eq(targets).sum().item() / len(targets)

    err_avg = 1 - (sum_corr/len(val_loader))
    loss_avg = sum_loss / len(val_loader)
    
    return err_avg, loss_avg





def initial(model, model_trained):

    state_dict = model_trained.state_dict()
    model.mu_std[0].load_state_dict(state_dict)
    model.w.load_state_dict(state_dict)

    for p in model.mu_std[1].parameters():
        p.data = 0.5*torch.log(torch.ones(p.shape) / 40).to(device)





def initial1(model, model_trained):

    state_dict = model_trained.state_dict()
    model.w.load_state_dict(state_dict)
    model.mu_std[0].load_state_dict(state_dict)

    for v, w in zip(model.mu_std[1].parameters(), model_trained.parameters()):
        v.data = 0.5*torch.log(torch.abs(w) / 10)


















#########################
###### trainining #######
#########################




def main():


    c = lenet
    rho = torch.tensor(-3).to(device).float()
    model = bayesian_nn(c,args)


    model_trained = lenet(*args)
    model_trained.load_state_dict(torch.load(path + "model.pt", map_location='cpu'))
    model_trained = model_trained.to(device)
    model_init = lenet(*args)
    model_init.load_state_dict(torch.load(path + "model_init.pt", map_location='cpu'))
    model_init = model_init.to(device)




    num_params = sum(p.numel() for p in model_trained.parameters())
    print(num_params)
    initial1(model, model_trained)


    # model_state, rho = torch.load(path + "model_bayes1.pt")
    # model.load_state_dict(model_state)
    # model = model.to(device)
    # rho = torch.tensor(rho).to(device)





    epochs = 100
    rho.requires_grad = True
    param = list(model.parameters()) + [rho]
    criterion = nn.CrossEntropyLoss().to(device)
    optimizer = optim.Adam(param, lr = 1e-3, weight_decay=0)




    dt = val_d(model_trained, device, train_loader, criterion, num_classes)
    bt = val(model, device, train_loader, criterion, num_classes)
    dv = val_d(model_trained, device, test_loader, criterion, num_classes)
    bv = val(model, device, test_loader, criterion, num_classes)

    print('deterministic train', dt)
    print('bayes train', bt)
    print('deterministic test', dv)
    print('bayes test', bv)


    loss2, kl, kl_1, kl_2, penalty = sec(model, model_init, rho, num_true, device)
    print("loss2, kl, kl1, kl2, p", loss2.item(), kl.item(), kl_1.item(), kl_2.item(), penalty.item())
    print("rho", rho.item())


    bd = approximate_BPAC_bound(1-bt[0], loss2.item())
    print("bd", bd)





    ## train loop

    for epoch in range(epochs):
        for g in optimizer.param_groups:
            g['lr'] = g['lr']*0.985


        time_start = time.time()

        train(model,model_init, num_true, device, train_loader, criterion, optimizer, rho, num_classes)
        time_end = time.time()

        if epoch%20 == 0:
            val_err, val_loss = val(model,device, test_loader, criterion, num_classes)
            train_err, train_loss = val(model,device, train_loader, criterion, num_classes)

            loss2, kl, kl_1, kl_2, penalty = sec(model, model_init, rho, num_true, device)
            bd1 = train_err + loss2
            bd2 = train_loss * (1/np.log(2)) + loss2



            print('epoch', epoch)
            print('train_err, train_loss', train_err, train_loss)
            print('val_err, val_loss', val_err, val_loss)
            print('bd1, bd2, rho', bd1.item(), bd2.item(), rho.item())
            print("loss2, kl, kl1, kl2, p", loss2.item(), kl.item(), kl_1.item(), kl_2.item(), penalty.item())
            for g in optimizer.param_groups:
                    print(g['lr'])
            print('time', time_end - time_start)

            if epoch != 0:
                torch.save((model.state_dict(), rho.item()), path + "model_bayes1.pt")





    ## statistics analysis

    dt = val_d(model_trained, device, train_loader, criterion, num_classes)
    bt = val(model, device, train_loader, criterion, num_classes)
    dv = val_d(model_trained, device, test_loader, criterion, num_classes)
    bv = val(model, device, test_loader, criterion, num_classes)

    print('deterministic train', dt)
    print('bayes train', bt)
    print('deterministic test', dv)
    print('bayes test', bv)


    loss2, kl, kl_1, kl_2, penalty = sec(model, model_init, rho, num_true, device)
    print("loss2, kl, kl1, kl2, p", loss2.item(), kl.item(), kl_1.item(), kl_2.item(), penalty.item())
    print("rho", rho.item())


    bd = approximate_BPAC_bound(1-bt[0], loss2.item())
    print("bd", bd)

    stat1 = dict({"dt": dt, "bt":bt, "dv":dv, "bv":bv, "bd":bd ,"loss2":loss2.item(), "kl":kl.item(), "kl_1":kl_1.item(), "kl_2":kl_2.item(), "rho":rho.item()})

    print(stat1)

    torch.save(stat1, path + "stat_bayes1.pt")




main()


























