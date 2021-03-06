import torch.utils.data as data
import torch
import pandas as pd
import os
import re
import logging
import numpy as np
import math
import Source.utils as utils
logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt = '%m/%d/%Y %H:%M:%S',
                    level = logging.INFO)
logger = logging.getLogger(__name__)


def cleanhtml(raw_html):
  cleanr = re.compile('<.*?>')
  cleantext = re.sub(cleanr, '', raw_html)
  return cleantext


# Truncate pair of sequences if longer than max_length
def _truncate_seq_pair_inv(tokens_a, tokens_b, max_length):
    while True:
        total_length = len(tokens_a) + len(tokens_b)
        if total_length <= max_length:
            break
        if len(tokens_a) > len(tokens_b):
            tokens_a.pop(0)
        else:
            tokens_b.pop()


# Class to contain a single instance of the dataset
class DataSample(object):
    def __init__(self,
                 qid,
                 question,
                 answer1,
                 answer2,
                 answer3,
                 answer4,
                 subtitles,
                 vision,
                 knowledge,
                 label):
        self.qid = qid
        self.question = question
        self.subtitles = subtitles
        self.kg = knowledge
        self.label = label
        self.vision = vision
        self.answers = [
            answer1,
            answer2,
            answer3,
            answer4,
        ]


# Load KnowIT VQA data
def load_knowit_data(args, split_name):
    input_file = ''
    if split_name == 'train':
        input_file = os.path.join(args.data_dir, 'knowit_data/knowit_data_train.csv')
    elif split_name == 'val':
        input_file = os.path.join(args.data_dir, 'knowit_data/knowit_data_val.csv')
    elif split_name == 'test':
        input_file = os.path.join(args.data_dir, 'knowit_data/knowit_data_test.csv')
    df = pd.read_csv(input_file, delimiter='\t')
    logger.info('Loaded file %s.' % (input_file))
    return df


# Dataloader for the Read Branch
class ReadBranchData(data.Dataset):

    def __init__(self, args, split, tokenizer):
        self.tokenizer = tokenizer
        self.max_seq_length = args.max_seq_length
        df = load_knowit_data(args, split)
        self.samples = self.get_data(df)
        self.num_samples = len(self.samples)
        logger.info('ReadBranchData ready with %d samples' % self.num_samples)


    # Load data into list of DataSamples
    def get_data(self, df):
        samples = []
        for index, row in df.iterrows():
            question = row['question']
            answer1 = row['answer1']
            answer2 = row['answer2']
            answer3 = row['answer3']
            answer4 = row['answer4']
            subtitles = cleanhtml(row['subtitle'].replace('<br />', ' ').replace(' - ', ' '))
            label = int(df['idxCorrect'].iloc[index] - 1)
            samples.append(DataSample(qid=index,
                                      question=question,
                                      answer1=answer1,
                                      answer2=answer2,
                                      answer3=answer3,
                                      answer4=answer4,
                                      subtitles = subtitles,
                                      vision=None,
                                      knowledge=None,
                                      label=label))
        return samples


    def __len__(self):
        return self.num_samples


    def __getitem__(self, index):
        # Convert each sample into 4 BERT input sequences as:
        # [CLS] + subtitles + question + [SEP] + answer1 + [SEP]
        # [CLS] + subtitles + question + [SEP] + answer2 + [SEP]
        # [CLS] + subtitles + question + [SEP] + answer3 + [SEP]
        # [CLS] + subtitles + question + [SEP] + answer4 + [SEP]

        sample = self.samples[index]
        subtitle_tokens = self.tokenizer.tokenize(sample.subtitles)
        question_tokens = self.tokenizer.tokenize(sample.question)
        choice_features = []
        for answer_index, answer in enumerate(sample.answers):

            start_tokens = subtitle_tokens[:] + question_tokens[:]
            ending_tokens = self.tokenizer.tokenize(answer)

            _truncate_seq_pair_inv(start_tokens, ending_tokens, self.max_seq_length - 3)
            tokens = [self.tokenizer.cls_token] + start_tokens + [self.tokenizer.sep_token] + ending_tokens + [self.tokenizer.sep_token]
            segment_ids = [0] * (len(start_tokens) + 2) + [1] * (len(ending_tokens) + 1)
            input_ids = self.tokenizer.convert_tokens_to_ids(tokens)
            input_mask = [1] * len(input_ids)

            padding = [self.tokenizer.pad_token_id] * (self.max_seq_length - len(input_ids))
            input_ids += padding
            input_mask += padding
            segment_ids += padding

            assert len(input_ids) == self.max_seq_length
            assert len(input_mask) == self.max_seq_length
            assert len(segment_ids) == self.max_seq_length

            choice_features.append((tokens, input_ids, input_mask, segment_ids))

        input_ids = torch.tensor([data[1] for data in choice_features], dtype=torch.long)
        input_mask = torch.tensor([data[2] for data in choice_features], dtype=torch.long)
        segment_ids = torch.tensor([data[3] for data in choice_features], dtype=torch.long)
        qid = torch.tensor(sample.qid, dtype=torch.long)
        label = torch.tensor(sample.label, dtype=torch.long)
        return input_ids, input_mask, segment_ids, qid, label


# Dataloader for the Read Branch
class ObserveBranchData(data.Dataset):

    def __init__(self, args, split, tokenizer):
        self.tokenizer = tokenizer
        self.max_seq_length = args.max_seq_length
        df = load_knowit_data(args, split)
        df_descriptions = pd.read_csv(os.path.join(args.descriptions_file), delimiter='\t')
        df_descriptions.replace(np.nan, '', inplace = True)
        self.samples = self.get_data(df, df_descriptions)
        self.num_samples = len(self.samples)
        logger.info('ReadBranchData ready with %d samples' % self.num_samples)


    # Load data into list of DataSamples
    def get_data(self, df, df_descriptions):
        samples = []
        for index, row in df.iterrows():
            question = row['question']
            answer1 = row['answer1']
            answer2 = row['answer2']
            answer3 = row['answer3']
            answer4 = row['answer4']
            label = int(df['idxCorrect'].iloc[index] - 1)

            # Get scene description
            scenename = row['scene']
            scene_description = ''
            if len(df_descriptions[df_descriptions['Scene'] == scenename]['Description']) > 0:
                scene_description = df_descriptions[df_descriptions['Scene'] == scenename]['Description'].values[0]

            samples.append(DataSample(qid=index,
                                      question=question,
                                      answer1=answer1,
                                      answer2=answer2,
                                      answer3=answer3,
                                      answer4=answer4,
                                      subtitles = None,
                                      vision=scene_description,
                                      knowledge=None,
                                      label=label))
        return samples


    def __len__(self):
        return self.num_samples


    def __getitem__(self, index):
        # Convert each sample into 4 BERT input sequences as:
        # [CLS] + scene description + question + [SEP] + answer1 + [SEP]
        # [CLS] + scene description + question + [SEP] + answer2 + [SEP]
        # [CLS] + scene description + question + [SEP] + answer3 + [SEP]
        # [CLS] + scene description + question + [SEP] + answer4 + [SEP]

        sample = self.samples[index]
        description_tokens = self.tokenizer.tokenize(sample.vision)
        question_tokens = self.tokenizer.tokenize(sample.question)
        choice_features = []
        for answer_index, answer in enumerate(sample.answers):

            start_tokens = description_tokens[:] + question_tokens[:]
            ending_tokens = self.tokenizer.tokenize(answer)

            _truncate_seq_pair_inv(start_tokens, ending_tokens, self.max_seq_length - 3)
            tokens = [self.tokenizer.cls_token] + start_tokens + [self.tokenizer.sep_token] + ending_tokens + [self.tokenizer.sep_token]
            segment_ids = [0] * (len(start_tokens) + 2) + [1] * (len(ending_tokens) + 1)
            input_ids = self.tokenizer.convert_tokens_to_ids(tokens)
            input_mask = [1] * len(input_ids)

            padding = [self.tokenizer.pad_token_id] * (self.max_seq_length - len(input_ids))
            input_ids += padding
            input_mask += padding
            segment_ids += padding

            assert len(input_ids) == self.max_seq_length
            assert len(input_mask) == self.max_seq_length
            assert len(segment_ids) == self.max_seq_length

            choice_features.append((tokens, input_ids, input_mask, segment_ids))

        input_ids = torch.tensor([data[1] for data in choice_features], dtype=torch.long)
        input_mask = torch.tensor([data[2] for data in choice_features], dtype=torch.long)
        segment_ids = torch.tensor([data[3] for data in choice_features], dtype=torch.long)
        qid = torch.tensor(sample.qid, dtype=torch.long)
        label = torch.tensor(sample.label, dtype=torch.long)
        return input_ids, input_mask, segment_ids, qid, label


# Dataloader for the Recall Branch
class RecallBranchData(data.Dataset):

    def __init__(self, args, split, tokenizer):
        self.tokenizer = tokenizer
        self.max_seq_length = args.max_seq_length
        self.num_max_slices = args.num_max_slices
        self.stride = args.seq_stride
        df = load_knowit_data(args, split)

        # Load file with the correspondent episodes to each video clip
        video_story_id_file = os.path.join(args.data_dir, 'knowledge_base/retrieved_episode_from_scenes_%s.csv' % split)
        self.dfretrieval = pd.read_csv(video_story_id_file, delimiter='\t')

        # Load episode summaries used as source of external knowledge
        dfkg = pd.read_csv(os.path.join(args.data_dir, 'knowledge_base/tbbt_summaries.csv'))
        self.recap_dict = dfkg.set_index('Episode').T.to_dict('list')

        # Prepare pairs
        self.samples = self.get_data(df)
        self.num_samples = len(self.samples)
        logger.info('RecallBranchData ready with %d samples' % self.num_samples)

    def get_data(self, df):
        samples = []
        for index, row in df.iterrows():
            question = row['question']
            answer1 = row['answer1']
            answer2 = row['answer2']
            answer3 = row['answer3']
            answer4 = row['answer4']
            label = int(df['idxCorrect'].iloc[index] - 1)

            # First find episode, then find episode summary
            episode = self.dfretrieval[self.dfretrieval['Scene'] == row['scene']]['Found Episode'].values[0]
            season = episode[1:3]
            number = episode[4:6]
            idepi = int(str(int(season)) + number)
            episode_summary = self.recap_dict[idepi][0]

            # Add sample to list
            samples.append(DataSample(qid=index,
                                      question=question,
                                      answer1=answer1,
                                      answer2=answer2,
                                      answer3=answer3,
                                      answer4=answer4,
                                      subtitles = None,
                                      vision=None,
                                      knowledge=episode_summary,
                                      label=label))

        return samples


    def __len__(self):
        return self.num_samples


    def __getitem__(self, index):
        # Convert each sample into 4*num_max_slices BERT input sequences as:
        # [CLS] + question + [SEP] + answer1 + kg_part_1 [SEP]
        # [CLS] + question + [SEP] + answer2 + kg_part_1 [SEP]
        # [CLS] + question + [SEP] + answer3 + kg_part_1 [SEP]
        # [CLS] + question + [SEP] + answer4 + kg_part_1 [SEP]
        # [CLS] + question + [SEP] + answer1 + kg_part_2 [SEP]
        # [CLS] + question + [SEP] + answer2 + kg_part_2 [SEP]
        # ...
        # [CLS] + question + [SEP] + answer4 + kg_part_num_max_slices [SEP]

        sample = self.samples[index]
        question_tokens = self.tokenizer.tokenize(sample.question)
        all_knowledge_tokens = self.tokenizer.tokenize(sample.kg)
        list_answer_tokens = []
        for answer in sample.answers:
            answer_tokens = self.tokenizer.tokenize(answer)
            list_answer_tokens.append(answer_tokens)

        # Compute maximum window length for knowledge slices based on question and answer lengths
        max_qa_len = len(question_tokens) + max([len(a) for a in list_answer_tokens])
        len_extra_tokens = 3
        len_kg_window = self.max_seq_length - max_qa_len - len_extra_tokens

        # Slice knowlegde according to window and stride
        list_knowledge_tokens = []
        num_kg_pieces = min(math.ceil((len(all_knowledge_tokens) - len_kg_window) / self.stride ) + 1, self.num_max_slices)
        num_kg_pieces = max(num_kg_pieces, 1)
        for n in list(range(num_kg_pieces)):
            maxpos = min(len_kg_window + (self.stride * n), len(all_knowledge_tokens))
            tokens = all_knowledge_tokens[self.stride*n:maxpos]
            list_knowledge_tokens.append(tokens)

        # Transformer input features
        sample_input_ids = np.zeros((self.num_max_slices, len(sample.answers), self.max_seq_length))
        sample_input_mask = np.zeros((self.num_max_slices, len(sample.answers), self.max_seq_length))
        sample_segment_ids = np.zeros((self.num_max_slices, len(sample.answers), self.max_seq_length))
        for kg_index, knowledge_tokens in enumerate(list_knowledge_tokens):
            for answer_index, answer_tokens in enumerate(list_answer_tokens):

                start_tokens = question_tokens[:]
                ending_tokens = answer_tokens + knowledge_tokens[:]

                sequence_tokens = [self.tokenizer.cls_token] + start_tokens + [self.tokenizer.sep_token] + ending_tokens + [self.tokenizer.sep_token]
                segment_ids = [0] * (len(start_tokens) + 2) + [1] * (len(ending_tokens) + 1)
                input_ids = self.tokenizer.convert_tokens_to_ids(sequence_tokens)
                input_mask = [1] * len(input_ids)

                padding = [self.tokenizer.pad_token_id] * (self.max_seq_length - len(input_ids))
                input_ids += padding
                input_mask += padding
                segment_ids += padding

                sample_input_ids[kg_index, answer_index, :] = input_ids
                sample_input_mask[kg_index, answer_index, :] = input_mask
                sample_segment_ids[kg_index, answer_index, :] = segment_ids

        sample_input_ids = torch.tensor(sample_input_ids, dtype=torch.long)
        sample_input_mask = torch.tensor(sample_input_mask, dtype=torch.long)
        sample_segment_ids = torch.tensor(sample_segment_ids, dtype=torch.long)
        qid = torch.tensor(sample.qid, dtype=torch.long)
        label = torch.tensor(sample.label, dtype=torch.long)
        return sample_input_ids, sample_input_mask, sample_segment_ids, qid, label


# Dataloader for branches fusion
class FusionDataloader(data.Dataset):

    def __init__(self, args, split):
        df = load_knowit_data(args, split)
        self.labels = (df['idxCorrect'] - 1).to_list()

        # Branches pre-computed features
        read_features = utils.load_obj(os.path.join(args.data_dir, '%s_embeddings' % args.dataset, 'read_branch_embeddings_%s.pckl' % split ))
        observe_features = utils.load_obj(os.path.join(args.data_dir, '%s_embeddings' % args.dataset, 'observe_branch_embeddings_%s.pckl' % split ))
        recall_features = utils.load_obj(os.path.join(args.data_dir, '%s_embeddings' % args.dataset, 'recall_branch_embeddings_%s.pckl' % split ))

        self.read_features = np.reshape(read_features, (int(read_features.shape[0]/4),4,768))
        self.observe_features = np.reshape(observe_features, (int(observe_features.shape[0]/4),4,768))
        self.recall_features = np.reshape(recall_features[0], (int(recall_features[0].shape[0]/4),5,4,768))
        self.recall_logits_slice = recall_features[1]

        self.num_samples = len(self.labels)
        logger.info('Dataloader with %d samples' % self.num_samples)

    def __len__(self):
        return self.num_samples

    def __getitem__(self, index):

        label = self.labels[index]
        outputs = [label, index]
        inputs = []

        in_read_feat = self.read_features[index,:]
        in_obs_feat = self.observe_features[index,:]
        recall_slices = self.recall_features[index,:]
        recall_logits_slice = self.recall_logits_slice[index,:]
        idx_slice, _ = np.unravel_index(recall_logits_slice.argmax(), recall_logits_slice.shape)
        in_recall_feat = recall_slices[idx_slice,:]
        inputs.extend([in_read_feat, in_obs_feat, in_recall_feat])

        return inputs, outputs