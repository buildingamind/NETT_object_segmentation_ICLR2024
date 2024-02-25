
#!/usr/bin/env python3

import pdb
import os
from sb3_contrib import RecurrentPPO
import torch

from stable_baselines3.common.logger import configure
from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.env_util import make_vec_env
from stable_baselines3.common.callbacks import CallbackList, CheckpointCallback, EvalCallback
from stable_baselines3.common.vec_env import VecFrameStack

import matplotlib.pyplot as plt
import pandas as pd


from callback.supervised_save_bestmodel_callback import SupervisedSaveBestModelCallback
from networks.encoder_config import ENCODERS
from utils import to_dict, write_to_file

from callback.hyperparam_callback import HParamCallback
from common.base_agent import BaseAgent
from GPUtil import getFirstAvailable

class SupervisedAgent(BaseAgent):
    def __init__(self, agent_id="Default Agent", \
        log_path="./Brains",
        **kwargs):
        super().__init__(agent_id, log_path, **kwargs)
        
        self.callback = SupervisedSaveBestModelCallback(summary_freq=self.summary_freq,\
            log_dir=self.path, \
            env_log_path = self.env_log_path, agent_id = self.id)
        
        self.hparamcallback = HParamCallback()
        self.checkpoint_callback = CheckpointCallback(save_freq=self.summary_freq,
                                                      save_path=os.path.join(self.path, "checkpoints"),
                                                      name_prefix="supervised_model",
                                                      save_replay_buffer=True,
                                                      save_vecnormalize=True)
        
        self.callback_list = CallbackList([self.callback, self.hparamcallback, self.checkpoint_callback])
        
        
        
    #Train an agent. Still need to allow exploration wrappers and non PPO rl algos.
    def train(self, env, eps):
        """
        Trains the agent using the specified environment and number of episodes.

        Args:
            env (gym.Env): The environment to train the agent on.
            eps (int): The number of episodes to train the agent for.
        """
        steps = env.steps_from_eps(eps)
        env = Monitor(env, self.path)
        
        try:
            self.check_env(env)
        except Exception as ex:
            print("Failed training env check",str(ex))
            return
        
        e_gen = lambda : env
        envs = make_vec_env(env_id=e_gen, n_envs=1, seed=self.seed)
        
        ## setup tensorboard logger
        new_logger = configure(self.path, ["stdout", "csv", "tensorboard"])
        self.model = self.setup_model(envs)
        self.model.set_logger(new_logger)
        
        ## Set Encoder requires grad
        if not self.train_encoder:
            self.model = self.set_feature_extractor_require_grad(self.model)
        
        ## write model properties to the file
        self.write_model_properties(self.model, steps)
        
        ## check if everything is initialized correctly        
        requires_grad_str = ""
        for param in self.model.policy.features_extractor.parameters():
            requires_grad_str+=str(param.requires_grad)
        
        print("Features Extractor Grad:"+ requires_grad_str)
        self.debug_logger.info("Training the agent")
        self.debug_logger.info(self.model)
        self.model.learn(total_timesteps=steps, tb_log_name=f"{self.id}",\
                         progress_bar=True,\
                         callback=[self.callback_list])
        
        self.save()
        del self.model
        self.model = None
        
        ## plot reward graph
        self.plot_results(steps, plot_name=f"reward_graph_{self.id}")
        # save encoder and policy network state dict - to perform model analysis
        self.save_encoder_policy_network()

    def create_model(self, policy_model, envs, policy_kwargs):
        """
        Creates a PPO model for the supervised agent.

        Args:
            policy_model (object): The policy model to be used.
            envs (object): The environment to interact with.
            policy_kwargs (dict): Additional keyword arguments for the policy.

        Returns:
            object: The recurrent model for the supervised agent.
        """
        return PPO(policy_model, envs, 
                batch_size=self.batch_size,
                n_steps=self.buffer_size,
                tensorboard_log=self.path,
                verbose=0, 
                policy_kwargs=policy_kwargs, 
                device=self.device)

    def create_recurrent_model(self, policy_model, envs, policy_kwargs):
        """
        Creates a recurrent PPO model for the supervised agent.

        Args:
            policy_model (object): The policy model to be used.
            envs (object): The environment to interact with.
            policy_kwargs (dict): Additional keyword arguments for the policy.

        Returns:
            object: The recurrent model for the supervised agent.
        """
        return RecurrentPPO(policy_model, envs, 
                            batch_size=self.batch_size,
                            n_steps=self.buffer_size,
                            tensorboard_log=self.path,
                            device=self.device, 
                            verbose=0,
                            policy_kwargs=policy_kwargs)
    

    def setup_model(self,envs):
        """
        Set up the model for the agent.

        Returns:
            The created model for the agent.
        """
        policy_model = "CnnPolicy" if self.policy.lower() == "ppo" else "CnnLstmPolicy"
        model_creator = self.create_model if self.policy.lower() == "ppo" else self.create_recurrent_model
        
        policy_kwargs = dict(features_extractor_kwargs=dict(features_dim=self.encoder_dim))
        if self.encoder_type == "small":
            policy_kwargs = {}
        else:
            policy_kwargs['features_extractor_class'] = ENCODERS[self.encoder_type]['encoder']

        
        ## each checkpoint corresponds to an imprinting condition.
        if self.encoder_type =="simclr":
            policy_kwargs["features_extractor_kwargs"]["object_background"] = self.object_background
        
        return model_creator(policy_model, envs, policy_kwargs)
