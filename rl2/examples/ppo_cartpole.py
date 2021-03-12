import gym
import marlenv
from marlenv.wrappers import SingleAgent
from rl2.agents.ppo import PPOModel, PPOAgent
from rl2.agents.ddpg import DDPGModel, DDPGAgent
from rl2.workers import MaxStepWorker


env = gym.make("CartPole-v0")
reorder = False

# env = gym.make("Snake-v1", num_snakes=1)
# env = SingleAgent(env)
# reorder = True

observation_shape = env.observation_space.shape
action_shape = (env.action_space.n,)

def ppo():
    model = PPOModel(observation_shape,
                    action_shape,
                    discrete=True,
                    reorder=reorder)
    train_interval = 512
    num_env = 1
    epoch = 4
    batch_size = 128
    agent = PPOAgent(model,
                    train_interval=train_interval,
                    batch_size=batch_size,
                    num_epochs=train_interval // batch_size * epoch,
                    buffer_kwargs={'size': train_interval * num_env})
    return agent

def ddpg():
    model = DDPGModel(observation_shape,
                    action_shape,
                    discrete=True,
                    reorder=reorder)
    train_interval = 1
    num_env = 1
    epoch = 1
    batch_size = 32
    agent = DDPGAgent(model,
                    train_interval=train_interval,
                    batch_size=batch_size,
                    num_epochs=epoch)
    return agent

agent = ppo()
worker = MaxStepWorker(env, agent, max_steps=int(2e5), training=True)

worker.run()

# TODO: vecenv, minibatch
