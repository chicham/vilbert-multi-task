import os

import torch
from torch.utils.data import Dataset
import numpy as np
import json

from pytorch_transformers.tokenization_bert import BertTokenizer
from ._image_features_reader import ImageFeaturesH5Reader
import _pickle as cPickle

from tools.refer.refer import REFER


def iou(anchors, gt_boxes):
    """
    anchors: (N, 4) ndarray of float
    gt_boxes: (K, 4) ndarray of float
    overlaps: (N, K) ndarray of overlap between boxes and query_boxes
    """
    N = anchors.size(0)
    K = gt_boxes.size(0)

    gt_boxes_area = (
        (gt_boxes[:, 2] - gt_boxes[:, 0] + 1) * (gt_boxes[:, 3] - gt_boxes[:, 1] + 1)
    ).view(1, K)

    anchors_area = (
        (anchors[:, 2] - anchors[:, 0] + 1) * (anchors[:, 3] - anchors[:, 1] + 1)
    ).view(N, 1)

    boxes = anchors.view(N, 1, 4).expand(N, K, 4)
    query_boxes = gt_boxes.view(1, K, 4).expand(N, K, 4)

    iw = (
        torch.min(boxes[:, :, 2], query_boxes[:, :, 2])
        - torch.max(boxes[:, :, 0], query_boxes[:, :, 0])
        + 1
    )
    iw[iw < 0] = 0

    ih = (
        torch.min(boxes[:, :, 3], query_boxes[:, :, 3])
        - torch.max(boxes[:, :, 1], query_boxes[:, :, 1])
        + 1
    )
    ih[ih < 0] = 0

    ua = anchors_area + gt_boxes_area - (iw * ih)
    overlaps = iw * ih / ua

    return overlaps


def assert_eq(real, expected):
    assert real == expected, "%s (true) vs %s (expected)" % (real, expected)


class Visual7wPointingDataset(Dataset):
    def __init__(
        self,
        task: str,
        dataroot: str,
        annotations_jsonpath: str,
        split: str,
        image_features_reader: ImageFeaturesH5Reader,
        gt_image_features_reader: ImageFeaturesH5Reader,
        tokenizer: BertTokenizer,
        bert_model,
        clean_datasets,
        padding_index: int = 0,
        max_seq_length: int = 20,
        max_region_num: int = 60,
    ):
        self.split = split
        self.num_labels = 1
        self._image_features_reader = image_features_reader
        self._gt_image_features_reader = gt_image_features_reader
        self._tokenizer = tokenizer

        self._padding_index = padding_index
        self._max_seq_length = max_seq_length
        self.dataroot = dataroot
        self.entries = self._load_annotations(clean_datasets)

        self.max_region_num = max_region_num
        clean_train = "_cleaned" if clean_datasets else ""

        if 'roberta' in bert_model:
            cache_path = os.path.join(
                dataroot, "cache", task + "_" + split + "_" + 'roberta' + "_" + str(max_seq_length) + "_" + str(max_region_num) + clean_train + ".pkl"
            )
        else:
            cache_path = os.path.join(
                dataroot, "cache", task + "_" + split + "_" + str(max_seq_length) + "_" + str(max_region_num) + clean_train + ".pkl"
            )

        if not os.path.exists(cache_path):
            self.tokenize()
            self.tensorize()
            cPickle.dump(self.entries, open(cache_path, "wb"))
        else:
            print("loading entries from %s" % (cache_path))
            self.entries = cPickle.load(open(cache_path, "rb"))

    def _load_annotations(self, clean_datasets):
        # Build an index which maps image id with a list of caption annotations.
        entries = []
        remove_ids = []
        if clean_datasets or self.split == 'mteval':
            remove_ids = np.load(os.path.join(self.dataroot, "cache", "genome_test_ids.npy"))
            remove_ids = [int(x) for x in remove_ids]

        with open(os.path.join(self.dataroot, "dataset_v7w_pointing.json"), "rb") as f:
            visual7w = json.load(f)
        boxes_dict = {}
        for b in visual7w['boxes']:
            boxes_dict[b['box_id']] = [b['x'], b['y'], b['x'] + b['width'], b['y'] + b['height']]
        if self.split == 'mteval':
            split = 'train'
        else:
            split = self.split
        for img in visual7w['images']:
            if img['split'] == split:
                if self.split == 'train' and int(img['image_id']) in remove_ids:
                    continue
                elif self.split == 'mteval' and int(img['image_id']) not in remove_ids:
                    continue
                bboxes = []
                for qa in img['qa_pairs']:
                    bboxes.extend(qa['multiple_choices'])
                    bboxes.append(qa['answer'])
                bboxes = list(set(bboxes))
                bboxes = sorted(bboxes)

                for qa in img['qa_pairs']:
                    bbox_idx = []
                    for a in sorted(qa['multiple_choices'] + [qa['answer']]):
                        bbox_idx.append(bboxes.index(a))
                    entries.append(
                        {
                            "caption": qa['question'],
                            "sent_id": qa['qa_id'],
                            "image_id": img['image_id'],
                            "refBox": boxes_dict[qa['answer']],
                            "ref_id": qa['answer'],
                            "mc_idx": bbox_idx,
                        }
                    )

        return entries

    def tokenize(self):
        """Tokenizes the captions.

        This will add caption_tokens in each entry of the dataset.
        -1 represents nil, and should be treated as padding_idx in embedding.
        """
        for entry in self.entries:

            # sentence_tokens = self._tokenizer.tokenize(entry["caption"])
            # sentence_tokens = ["[CLS]"] + sentence_tokens + ["[SEP]"]

            # tokens = [
            #     self._tokenizer.vocab.get(w, self._tokenizer.vocab["[UNK]"])
            #     for w in sentence_tokens
            # ]

            tokens = self._tokenizer.encode(entry["caption"])
            tokens = tokens[: self._max_seq_length-2]
            tokens = self._tokenizer.add_special_tokens_single_sentence(tokens)

            segment_ids = [0] * len(tokens)
            input_mask = [1] * len(tokens)

            if len(tokens) < self._max_seq_length:
                # Note here we pad in front of the sentence
                padding = [self._padding_index] * (self._max_seq_length - len(tokens))
                tokens = tokens + padding
                input_mask += padding
                segment_ids += padding

            assert_eq(len(tokens), self._max_seq_length)
            entry["token"] = tokens
            entry["input_mask"] = input_mask
            entry["segment_ids"] = segment_ids

    def tensorize(self):

        for entry in self.entries:
            token = torch.from_numpy(np.array(entry["token"]))
            entry["token"] = token

            input_mask = torch.from_numpy(np.array(entry["input_mask"]))
            entry["input_mask"] = input_mask

            segment_ids = torch.from_numpy(np.array(entry["segment_ids"]))
            entry["segment_ids"] = segment_ids

    def __getitem__(self, index):
        entry = self.entries[index]

        image_id = entry["image_id"]
        ref_box = entry["refBox"]
        multiple_choice_idx = torch.from_numpy(np.array(entry["mc_idx"]))

        features, num_boxes, boxes, boxes_ori = self._image_features_reader["v7w_" + str(image_id)]

        boxes_ori = boxes_ori[:num_boxes]
        boxes = boxes[:num_boxes]
        features = features[:num_boxes]

        gt_features, gt_num_boxes, gt_boxes, gt_boxes_ori = self._gt_image_features_reader[
            "v7w_" + str(image_id)
        ]

        # merge two boxes, and assign the labels.
        gt_boxes_ori = gt_boxes_ori[1:gt_num_boxes]
        gt_boxes = gt_boxes[1:gt_num_boxes]
        gt_features = gt_features[1:gt_num_boxes]

        # concatenate the boxes
        mix_boxes_ori = np.concatenate((boxes_ori, gt_boxes_ori), axis=0)
        mix_boxes = np.concatenate((boxes, gt_boxes), axis=0)
        mix_features = np.concatenate((features, gt_features), axis=0)
        mix_num_boxes = min(
            int(num_boxes + int(gt_num_boxes) - 1), self.max_region_num
        )
        # given the mix boxes, and ref_box, calculate the overlap.
        target = iou(
            torch.tensor(mix_boxes_ori[:, :4]).float(),
            torch.tensor([ref_box]).float(),
        )
        target[target < 0.5] = 0
        # Only require the multiple choice bbox targets for calculating loss
        target = target[101:]
        target = target[multiple_choice_idx]

        image_mask = [1] * (mix_num_boxes)
        while len(image_mask) < self.max_region_num:
            image_mask.append(0)

        mix_boxes_pad = np.zeros((self.max_region_num, 5))
        mix_features_pad = np.zeros((self.max_region_num, 2048))

        mix_boxes_pad[:mix_num_boxes] = mix_boxes[:mix_num_boxes]
        mix_features_pad[:mix_num_boxes] = mix_features[:mix_num_boxes]

        # appending the target feature.
        features = torch.tensor(mix_features_pad).float()
        image_mask = torch.tensor(image_mask).long()
        spatials = torch.tensor(mix_boxes_pad).float()

        spatials_ori = torch.tensor(mix_boxes_ori).float()
        co_attention_mask = torch.zeros((self.max_region_num, self._max_seq_length))

        caption = entry["token"]
        input_mask = entry["input_mask"]
        segment_ids = entry["segment_ids"]

        return (
            features,
            spatials,
            image_mask,
            caption,
            target,
            input_mask,
            segment_ids,
            multiple_choice_idx,
            co_attention_mask,
            image_id,
        )

    def __len__(self):
        return len(self.entries)
