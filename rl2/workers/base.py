import os
import pickle

import numpy as np
from pathlib import Path
from collections import deque

import logging
# from logging import getLogger, Logger

from rl2.agents.base import Agent
import signal
from datetime import datetime


from rl2.utils import EasyDict

# train_config = EasyDict()
# train_config.save_interval = 0
# train_config.train_interval = 0
# train_config.optimizer = 'torch.optim.RMSprop'
#
# logger_config = EasyDict()
# logger_config.log_interval = 0
# logger_config.log_dir = 0
# logger_config.log_level = 0

import time


class Timer:
    def __enter__(self):
        self.start = time.clock()
        return self

    def __exit__(self, *args):
        self.end = time.clock()
        self.interval = self.end - self.start


class RolloutWorker:
    """
    workers mimics the intuitive loop btw agent and env

    if worker is to serve as some entrypoint for an app,
    worker might need context so everything is under control of workers

    rl2's base unit is a step(1 interaction per se)
    """

    def __init__(
            self,
            env,
            agent,
            num_envs=1,
            train_config=None,
            render_interval=0,
            save_model=False,
            save_erange=None,
            save_interval=None,
            **kwargs
    ):
        self.env = env
        self.agent = agent
        self.num_envs = num_envs
        if train_config is not None:
            self.set_mode(train=True)
            self.train_config = train_config
        else:
            self.set_mode(train=False)

        # self.training = training

        # self.render = render
        # if self.render:
        self.render_interval = render_interval
        self.render_mode = kwargs.get('render_mode', 'rgb_array')

        # self.is_save = is_save
        # if self.save_interval > 0:
        self.save_interval = save_interval

        self.curr_episode = 0
        self.num_steps = 0

        self.scores = deque(maxlen=100)
        self.episode_length = deque(maxlen=100)
        self.episode_score = 0
        self.episode_steps = 0

        self.obs = env.reset()


        self.save_erange = save_erange
        self.curr_trajectory = []
        self.trajectories = []
        # self.save_trajectory = False

        self.saving_model = True

        # TODO: for now
        # logger_name = __name__
        # self.logger = Logger(logger_name)
        # print(self.logger)
        # self.logger = getLogger(logger_name)

    def run(self):
        raise NotImplementedError


    def __enter__(self):
        self.start_dt = time.clock()

        # TODO: [feature] try to attach ipython session
        if self.saving_model is True:
            signal.signal(signal.SIGINT, lambda sig, frame: self.save())

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_dt = time.clock()

        if self.saving_model is True:
            try:
                self.save()
            except Exception as e:
                print(e)

        logging.info(f'Time elapsed {self.end_dt - self.start_dt}.')
        logging.info(f'Ran from {self.start_dt} to {self.end_dt}.')


    @property
    def running_time(self):
        return self.end_dt - self.start_dt

    def set_mode(self, train=True):
        # for torch currently
        if train is True:
            self.train_mode = True
            # self.agent.model.train()
        else:
            self.train_mode = False
            # self.agent.model.eval()

    def as_saving(self, all=True):
        self.saving_model = True

        return self

    def default_save_dir(self):
        return f"""{type(self).__name__}_{datetime.now().strftime('%Y%m%d%H%M%S')}"""

    def save(self, save_dir=None):
        if save_dir is None:
            save_dir = self.default_save_dir()

        save_dir = os.path.join(save_dir, f'ckpt/{int(self.num_steps / 1000)}k')
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        self.agent.model.save(save_dir)

        logging.info(f'saved model to {save_dir}')

        return save_dir

    def save_expert_data(self, save_dir=None):
        if save_dir is None:
            save_dir = os.path.join(self.default_save_dir(), 'expert_data')
            Path(save_dir).mkdir(parents=True, exist_ok=True)

            filename = f'{type(self.agent).__name__}_trajs.pickle'
            save_dir = os.path.join(save_dir, filename)

        with open(save_dir, 'wb') as fp:
            pickle.dump(self.trajectories, fp)

        logging.info(f'saved expert trajectories to {save_dir}')

        return save_dir

    def rollout(self):
        action = self.agent.act(self.obs)
        if self.in_erange() is True:
            # S_t, A_t
            self.curr_trajectory.append((self.obs, action))

        if hasattr(action, 'shape'):
            if len(action.shape) == 2 and action.shape[0] == 1:
                action = action.squeeze(0)
        obs, rew, done, info = self.env.step(action)

        if self.train_mode is True:
            info_a = self.agent.step(self.obs, action, rew, done, obs)
            if info:
                if isinstance(info, dict):
                    info = {**info, **info_a}
                elif isinstance(info, list) or isinstance(info, tuple):
                    info = {**info_a}
            else:
                info = {**info_a}

        self.num_steps += self.num_envs
        self.episode_score = self.episode_score + np.array(rew)
        self.episode_steps = self.episode_steps + np.ones_like(done, np.int)

        done = np.asarray(done)

        if done.size > 1:
            self.curr_episode += sum(done)
            for idx in np.where(done)[0]:
                self.scores.append(self.episode_score[idx])
                self.episode_score[idx] = 0.
                self.episode_length.append(self.episode_steps[idx])
                self.episode_steps[idx] = 0.
        else:
            if done:
                # episode changes from below
                obs = self.env.reset()
                self.scores.append(self.episode_score)
                self.episode_score = 0.
                self.episode_length.append(self.episode_steps)
                self.episode_steps = 0

                if self.in_erange() is True:
                    self.trajectories.append(self.curr_trajectory)
                    self.curr_trajectory = []

                self.curr_episode += 1

        self.obs = obs
        results = None

        return done, info, results

    def in_erange(self):
        if self.save_erange is None:
            return False
        return self.save_erange[0] <= self.curr_episode < self.save_erange[1]


class MaxStepWorker(RolloutWorker):
    """
    do rollout until max steps given
    """

    def __init__(
            self,
            env,
            agent,
            max_steps=1000,
            logger=None,
            log_interval=5000,
            **kwargs
    ):
        super().__init__(env, agent, **kwargs)
        self.max_steps = int(max_steps)
        self.log_interval = int(log_interval)
        self.logger = logger
        self.info = {}
        self.store_image = False
        self.start_log_image = False
        self.time_to_log_image = False

    def run(self):

        steps_per_env = (self.max_steps // self.num_envs) + 1
        for step in range(steps_per_env):
            done, info, results = self.rollout()


            self.worker_log(done)

            if self.in_save_interval() is True:
                self.save_model()

    def in_save_interval(self):
        return (self.save_interval > 0) and ((self.num_steps % self.save_interval) < self.num_envs)

    def save_model(self):

        if hasattr(self, 'logger'):
            save_dir = getattr(self.logger, 'log_dir')
        else:
            save_dir = os.getcwd()
        self.save(save_dir)

    def worker_log(self, done):
        # Save rendered image as gif
        if self.render_interval > 0:
            if (self.num_steps % self.render_interval) < self.num_envs:
                self.time_to_log_image = True

            cond = done if np.asarray(done).size == 1 else done[0]
            if cond:
                if self.time_to_log_image:
                    self.time_to_log_image = False
                    self.start_log_image = True
                elif self.start_log_image:
                    self.start_log_image = False
                    self.store_image = True

            if self.start_log_image:
                image = self.env.render(self.render_mode)
                self.logger.store_rgb(image)
            elif self.store_image:
                self.logger.video_summary(tag='playback',
                                          step=self.num_steps)
                self.store_image = False
        # Log info
        if self.num_steps % self.log_interval < self.num_envs:
            info_r = {
                'Counts/num_steps': self.num_steps,
                'Counts/num_episodes': self.curr_episode,
                'Episodic/rews_avg': np.mean(list(self.scores)),
                'Episodic/ep_length': np.mean(list(self.episode_length))
            }
            self.info.update(info_r)
            # self.info.update(info)
            self.logger.scalar_summary(self.info, self.num_steps)


class EpisodicWorker(RolloutWorker):
    """
    do rollout until max episodes given
    might be useful at inference time or when training episodically
    """

    def __init__(
            self,
            env,
            agent,
            max_episodes: int = 10,
            log_interval: int = 1,
            logger=None,
            **kwargs
    ):
        super().__init__(env, agent, **kwargs)
        self.max_episodes = int(max_episodes)
        self.log_interval = int(log_interval)
        self.logger = logger
        self.info = {}
        self.store_image = False
        self.start_log_image = False

    def run(self):
        while self.curr_episode < self.max_episodes:
            # print(f'running episode {self.curr_episode}...')
            prev_num_ep = self.curr_episode
            done, info, results = self.rollout()

            # self.worker_log(done, prev_num_ep)

    def worker_log(self, done, prev_num_ep):
        if self.render_interval > 0 and self.start_log_image:
            image = self.env.render(self.render_mode)
            self.logger.store_rgb(image)
        log_cond = done if np.asarray(done).size == 1 else any(done)
        if log_cond:
            if self.start_log_image:
                print('save video')
                self.logger.video_summary(tag='playback',
                                          step=self.num_steps)
                self.start_log_image = False
            if self.render_interval > 0:
                if (prev_num_ep // self.render_interval !=
                        self.curr_episode // self.render_interval):
                    self.start_log_image = True
            info_r = {
                'Counts/num_steps': self.num_steps,
                'Counts/num_episodes': self.curr_episode,
                'Episodic/rews_avg': np.mean(list(self.scores)),
                'Episodic/ep_length': np.mean(list(self.episode_length))
            }
            self.info.update(info_r)
            # self.info.update(info)
            if (prev_num_ep // self.log_interval
                    != self.curr_episode // self.log_interval):
                self.logger.scalar_summary(self.info, self.num_steps)