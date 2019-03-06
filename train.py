import json
import torch
import pandas as pd
from sqlnet.utils import *
from sqlnet.model.seq2sql import Seq2SQL
from sqlnet.model.sqlnet import SQLNet
import numpy as np
import datetime

import argparse

if __name__ == '__main__':
    columns = ['Epoch', 'Type', 'Loss', 'Train Acc', 'Val Acc']
    results = pd.DataFrame({i:[] for i in columns})

    parser = argparse.ArgumentParser()
    parser.add_argument('--toy', action='store_true',
            help='If set, use small data; used for fast debugging.')
    parser.add_argument('--suffix', type=str, default='',
            help='The suffix at the end of saved model name.')
    parser.add_argument('--ca', action='store_true',
            help='Use conditional attention.')
    parser.add_argument('--dataset', type=int, default=0,
            help='0: original dataset, 1: re-split dataset')
    parser.add_argument('--rl', action='store_true',
            help='Use RL for Seq2SQL(requires pretrained model).')
    parser.add_argument('--baseline', action='store_true',
            help='If set, then train Seq2SQL model; default is SQLNet model.')
    parser.add_argument('--train_emb', action='store_true',
            help='Train word embedding for SQLNet(requires pretrained model).')
    args = parser.parse_args()

    params = vars(args)

    for opt in ['ca', 'rl', 'baseline', 'train_emb']:
        params[opt] = True
        N_word=300
        B_word=42
        if params['toy']:
            USE_SMALL=True
            GPU=True
            BATCH_SIZE=15
        else:
            USE_SMALL=False
            GPU=True
            BATCH_SIZE=64
        TRAIN_ENTRY=(True, True, True)  # (AGG, SEL, COND)
        TRAIN_AGG, TRAIN_SEL, TRAIN_COND = TRAIN_ENTRY
        learning_rate = 1e-4 if params['rl'] else 1e-3

        sql_data, table_data, val_sql_data, val_table_data, \
                test_sql_data, test_table_data, \
                TRAIN_DB, DEV_DB, TEST_DB = load_dataset(
                        params['dataset'], use_small=USE_SMALL)

        word_emb = load_word_emb('glove/glove.%dB.%dd.txt'%(B_word,N_word), \
                load_used=params['train_emb'], use_small=USE_SMALL)

        if params['baseline']:
            model = Seq2SQL(word_emb, N_word=N_word, gpu=GPU,
                    trainable_emb = params['train_emb'])
            assert not params['train_emb'], "Seq2SQL can\'t train embedding."
        else:
            model = SQLNet(word_emb, N_word=N_word, use_ca=params['ca'],
                    gpu=GPU, trainable_emb = params['train_emb'])
            assert not params['rl'], "SQLNet can\'t do reinforcement learning."
        optimizer = torch.optim.Adam(model.parameters(),
                lr=learning_rate, weight_decay = 0)

        if params['train_emb']:
            agg_m, sel_m, cond_m, agg_e, sel_e, cond_e = best_model_name(args)
        else:
            agg_m, sel_m, cond_m = best_model_name(args)

        if params['rl'] or params['train_emb']: # Load pretrained model.
            agg_lm, sel_lm, cond_lm = best_model_name(args, for_load=True)
            print "Loading from %s"%agg_lm
            model.agg_pred.load_state_dict(torch.load(agg_lm))
            print "Loading from %s"%sel_lm
            model.sel_pred.load_state_dict(torch.load(sel_lm))
            print "Loading from %s"%cond_lm
            model.cond_pred.load_state_dict(torch.load(cond_lm))

        if params['rl']:
            best_acc = 0.0
            best_idx = -1
            print "Init dev acc_qm: %s\n  breakdown on (agg, sel, where): %s"% \
                    epoch_acc(model, BATCH_SIZE, val_sql_data,\
                    val_table_data, TRAIN_ENTRY)
            print "Init dev acc_ex: %s"%epoch_exec_acc(
                    model, BATCH_SIZE, val_sql_data, val_table_data, DEV_DB)
            torch.save(model.cond_pred.state_dict(), cond_m)
            for i in range(25): # 25 epochs
                print 'Epoch %d @ %s'%(i+1, datetime.datetime.now())
                avg_reward = epoch_reinforce_train(model, optimizer, BATCH_SIZE, sql_data, table_data, TRAIN_DB)
                print ' Avg reward = %s'%avg_reward
                dev_acc, breakdown = epoch_acc(model, BATCH_SIZE, val_sql_data, val_table_data, TRAIN_ENTRY)
                print ' dev acc_qm: %s\n   breakdown result: %s'% (dev_acc, breakdown)
                exec_acc = epoch_exec_acc(model, BATCH_SIZE, val_sql_data, val_table_data, DEV_DB)
                print ' dev acc_ex: %s', exec_acc

                epoch = pd.Series([i+1, opt, avg_reward, dev_acc, exec_acc], index=columns)
                results = results.append(epoch)
                if exec_acc[0] > best_acc:
                    best_acc = exec_acc[0]
                    best_idx = i+1
                    torch.save(model.cond_pred.state_dict(),
                            'saved_model/epoch%d.cond_model%s'%(i+1, params['suffix']))
                    torch.save(model.cond_pred.state_dict(), cond_m)
                print ' Best exec acc = %s, on epoch %s'%(best_acc, best_idx)
        else:
            init_acc = epoch_acc(model, BATCH_SIZE,
                    val_sql_data, val_table_data, TRAIN_ENTRY)
            best_agg_acc = init_acc[1][0]
            best_agg_idx = 0
            best_sel_acc = init_acc[1][1]
            best_sel_idx = 0
            best_cond_acc = init_acc[1][2]
            best_cond_idx = 0
            print 'Init dev acc_qm: %s\n  breakdown on (agg, sel, where): %s'%\
                    init_acc
            if TRAIN_AGG:
                torch.save(model.agg_pred.state_dict(), agg_m)
                if params['train_emb']:
                    torch.save(model.agg_embed_layer.state_dict(), agg_e)
            if TRAIN_SEL:
                torch.save(model.sel_pred.state_dict(), sel_m)
                if params['train_emb']:
                    torch.save(model.sel_embed_layer.state_dict(), sel_e)
            if TRAIN_COND:
                torch.save(model.cond_pred.state_dict(), cond_m)
                if params['train_emb']:
                    torch.save(model.cond_embed_layer.state_dict(), cond_e)
            for i in range(25): # 25 epochs
                print 'Epoch %d @ %s'%(i+1, datetime.datetime.now())
                loss = epoch_train(model, optimizer, BATCH_SIZE,sql_data, table_data, TRAIN_ENTRY)
                print ' Loss = %s'%loss
                train_acc, breakdown = epoch_acc(model, BATCH_SIZE, sql_data, table_data, TRAIN_ENTRY)
                print ' Train acc_qm: %s\n   breakdown result: %s'%(train_acc, breakdown)
                #val_acc = epoch_token_acc(model, BATCH_SIZE, val_sql_data, val_table_data, TRAIN_ENTRY)
                val_acc = epoch_acc(model,BATCH_SIZE, val_sql_data, val_table_data, TRAIN_ENTRY)
                print ' Dev acc_qm: %s\n   breakdown result: %s'%val_acc

                epoch = pd.Series([i+1, opt, loss, train_acc, val_acc], index=columns)
                results = results.append(epoch)
                if TRAIN_AGG:
                    if val_acc[1][0] > best_agg_acc:
                        best_agg_acc = val_acc[1][0]
                        best_agg_idx = i+1
                        torch.save(model.agg_pred.state_dict(),
                            'saved_model/epoch%d.agg_model%s'%(i+1, params['suffix']))
                        torch.save(model.agg_pred.state_dict(), agg_m)
                        if params['train_emb']:
                            torch.save(model.agg_embed_layer.state_dict(),
                            'saved_model/epoch%d.agg_embed%s'%(i+1, params['suffix']))
                            torch.save(model.agg_embed_layer.state_dict(), agg_e)
                if TRAIN_SEL:
                    if val_acc[1][1] > best_sel_acc:
                        best_sel_acc = val_acc[1][1]
                        best_sel_idx = i+1
                        torch.save(model.sel_pred.state_dict(),
                            'saved_model/epoch%d.sel_model%s'%(i+1, params['suffix']))
                        torch.save(model.sel_pred.state_dict(), sel_m)
                        if params['train_emb']:
                            torch.save(model.sel_embed_layer.state_dict(),
                            'saved_model/epoch%d.sel_embed%s'%(i+1, params['suffix']))
                            torch.save(model.sel_embed_layer.state_dict(), sel_e)
                if TRAIN_COND:
                    if val_acc[1][2] > best_cond_acc:
                        best_cond_acc = val_acc[1][2]
                        best_cond_idx = i+1
                        torch.save(model.cond_pred.state_dict(),
                            'saved_model/epoch%d.cond_model%s'%(i+1, params['suffix']))
                        torch.save(model.cond_pred.state_dict(), cond_m)
                        if params['train_emb']:
                            torch.save(model.cond_embed_layer.state_dict(),
                            'saved_model/epoch%d.cond_embed%s'%(i+1, params['suffix']))
                            torch.save(model.cond_embed_layer.state_dict(), cond_e)
                print ' Best val acc = %s, on epoch %s individually'%(
                        (best_agg_acc, best_sel_acc, best_cond_acc),
                        (best_agg_idx, best_sel_idx, best_cond_idx))
        results.to_csv('results.csv')
