from model import Model
from bandits import BanditAlgorithm
import random
import time
import cPickle as pickle
from collections import Counter, deque
import numpy as np

# TODO KILL ALL UNSED CODE!!
# TODO Try prioritazed replay, and also target network
# TODO Reward clipping (clip reward between [-1,1]


def learn_Q_function(all_observed_decision_states, reward, model):
    """
    Episodic learning (mostly for lookup table method) - helper method
    """
    if model.model_class == 'lookup_table':
        model.fit(all_observed_decision_states, reward)

    elif model.model_class == 'scikit' or model.model_class == 'vw' or model.model_class == 'vw_python':
        for decision_state in all_observed_decision_states:
            X_new, y_new = model.return_design_matrix(decision_state, reward)
            model.X.append(X_new)
            model.y.append(y_new)

        if model.buffer == 1000:
            model.fit(model.X, model.y)

            # TODO Instead of killing entire buffer we can keep a few and kill only the subset
            model.clean_buffer()

    return model


def train_reinforcement_learning_strategy(num_sims=1, game_obs='blackjack', model_class='lookup_table'):
    """
    Episodic learning (mostly for lookup table method)
    """
    start_time = time.time()
    # Initialize model
    model = Model({'class': model_class, 'base_folder_name': game_obs.base_folder_name})
    banditAlgorithm = BanditAlgorithm(params=0.1)
    model.initialize()

    model.all_possible_decisions = game_obs.action_space

    for _ in xrange(num_sims):
        model.buffer += 1

        # Initialize game
        game_obs.reset()
        if game_obs.game_status != 'in process':
            continue

        all_observed_decision_states, reward = game_obs.complete_one_episode(banditAlgorithm, model)
        model = learn_Q_function(all_observed_decision_states, reward, model)

    model.finish()
    elapsed_time = int(time.time() - start_time)
    print ": took time:" + str(elapsed_time)
    return banditAlgorithm.policy, model


class RLAgent(object):

    def __init__(self, epochs, experience_replay_size, batchsize, gamma, skip_frames, max_steps):
        self.epochs = epochs
        self.experience_replay_size = experience_replay_size
        self.experience_replay = deque(maxlen=experience_replay_size)
        self.batchsize = batchsize
        self.gamma = gamma
        self.max_steps = max_steps
        self.skip_frames = skip_frames
        self.frames = deque(maxlen=skip_frames)

    def initialize(self, model_params, bandit_params, test=False):
        """
        Initialize model
        Initialize bandit_algorithm
        :param model_params:
        :param bandit_params:
        :return:
        """
        if not test:
            model = Model(model_params)
            model.initialize()

        else:
            model = pickle.load(open(model_params['base_folder_name'] + '/model_obs.pkl', mode='rb'))
            if model.model_class == 'vw_python':
                from vowpal_wabbit import pyvw
                model.model = pyvw.vw("--quiet -i {0}".format(model.model_path))

        bandit_algorithm = BanditAlgorithm(params=bandit_params)

        return model, bandit_algorithm

    def train_q_function(self, env, model, bandit_algorithm):
        """
        Simple temporal difference learning
        with experience-replay
        :return:
        """
        for episode in xrange(self.epochs):

            # Initialize game and parameters for this epoch
            env.reset()
            done = None
            total_reward = 0

            # Reset epsilon
            #banditAlgorithm.params = epsilon
            print 'Game #: {0}'.format(episode)

            # Start playing the game
            for move in xrange(self.max_steps):

                # Check game status and breakout if you have a result
                if done:
                    break

                # Store current game state
                old_state = env.state if env.state else None

                # Figure out best action based on policy
                best_known_decision, known_reward = bandit_algorithm.select_decision_given_state(env.state, env.action_space, model,
                                                                                                algorithm='epsilon-greedy')

                for _ in xrange(self.skip_frames):
                    # Make move to get to a new state and observe reward (Remember this could be a terminal state)
                    observation, reward, done, info = env.step(best_known_decision)
                    total_reward += reward
                    self.frames.appendleft(observation)
                    if _ == (self.skip_frames-1):
                        new_state = tuple(fea for frm in self.frames for fea in frm)
                        env.state = new_state
                        self.frames.clear()

                # Experience replay storage (deque object maintains a queue, so no extra processing needed)
                # If buffer is full, it gets overwritten due to deque magic
                if old_state and new_state:
                    self.experience_replay.appendleft((old_state, best_known_decision, reward, new_state))

                # TODO Why can't we keep on allocating observed rewards to previous steps (using TD-lambda rule except the last step of estimation)
                # If buffer not filled, continue and collect sample to train
                if (len(self.experience_replay) < self.experience_replay_size):
                    continue

                # Start training only after buffer is full
                else:

                    # randomly sample our experience replay memory
                    # TODO Prioratized replay can be implemented with np.random.choice
                    # We can create Russian roulette a.k.a importance sampling based on the reward
                    total_reward_in_ex_replay = sum(max(abs(st[2]), 0.1) for st in self.experience_replay)
                    probs = tuple((max(abs(st[2]), 0.1)*1.0/total_reward_in_ex_replay for st in self.experience_replay))
                    selection = set(np.random.choice(range(self.experience_replay_size), self.batchsize, probs))
                    # minibatch = random.sample(self.experience_replay, self.batchsize)
                    minibatch = [j for i,j in enumerate(self.experience_replay) if i in selection]

                    # Now for each gameplay experience, update current reward based on the future reward (using action given by the model)
                    for memory_lst in minibatch:
                        old_state_er, action_er, reward_er, new_state_er = memory_lst

                        # If game hasn't finished OR if no model then we have to update the reward based on future discounted reward
                        if not done and model.exists:  # non-terminal state
                            # Get value estimate for that best action and update EXISTING reward
                            result = bandit_algorithm.return_action_based_on_greedy_policy(new_state_er, model, env.action_space)
                            max_reward = result[1]

                            # Update reward for the current step AND for last n stapes (if n is large, we deploy TD-lambda)
                            # TODO TD-lambda
                            if result:
                                reward_er += self.gamma * max_reward

                        # Design matrix is based on estimate of reward at state,action step t+1
                        X_new, y_new = model.return_design_matrix((old_state_er, action_er), reward_er)
                        model.X.append(X_new)
                        model.y.append(y_new)

                    # We are retraining in every single epoch, but with some subset of all samples
                    model.fit(model.X, model.y)
                    model.clean_buffer()

            if bandit_algorithm.params > 0.1:  # decrement epsilon over time
                bandit_algorithm.params -= (1.0 / self.epochs)

            print 'Total reward #: {0}'.format(total_reward)

        model.finish()
        #elapsed_time = int(time.time() - start_time)
        return bandit_algorithm.policy, model

    def test_q_function(self, env, model, bandit_algorithm):
        print "---------- Testing policy:-----------"

        random_stat = self.test_q_function_with_model(env, bandit_algorithm, model='random')
        model_stat = self.test_q_function_with_model(env, bandit_algorithm, model=model)

        return random_stat, model_stat

    def test_q_function_with_model(self, env, bandit_algorithm, model='random'):
        result = Counter()
        for episode in xrange(100):
            env.reset()
            done = False
            per_episode_result = Counter()
            for mv in xrange(1, 11):
                if model=='random':
                    action = random.choice(env.action_space)
                else:
                    action, value_estimate = bandit_algorithm.return_action_based_on_greedy_policy(env.state, model, env.action_space)

                for _ in xrange(self.skip_frames):
                    observation, reward, done, info = env.step(action)
                    per_episode_result[episode] += reward
                    self.frames.appendleft(observation)
                    if _ == (self.skip_frames - 1):
                        new_state = tuple(fea for frm in self.frames for fea in frm)
                        env.state = new_state
                        self.frames.clear()

                if done:
                    if reward > 0:
                        result['player wins'] += 1
                    else:
                        result['player loses'] += 1
                    break

            if not done:
                result['in process'] += 1

        result['average reward'] = sum(per_episode_result.itervalues()) / 100
        return result