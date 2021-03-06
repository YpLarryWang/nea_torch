import numpy as np
import torch
import torch.nn as nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence


class Model(nn.Module):
    def __init__(self, config):
        super(Model, self).__init__()

        # setting embedding layer
        if config.embedding_pre is not None:
            self.embedding = nn.Embedding.from_pretrained(config.embedding_pre, freeze=False)
        else:
            self.embedding = nn.Embedding(config.vocab_size, config.emb_dim, padding_idx=0)

        # setting cnn layer
        self.conv = nn.Conv2d(1, config.num_filters, (config.window_size, config.emb_dim),
                              stride=config.cnn_stride, padding=config.cnn_pad_size)

        # setting rnn layer
        self.rnn_type = config.rnn_type

        if config.rnn_type in ['LSTM', 'GRU']:
            self.rnn = getattr(nn, config.rnn_type)(config.num_filters, config.hidden_size,
                                                    config.rnn_nlayers, dropout=config.dropout_U)
        else:
            try:
                nonlinearity = {'RNN_TANH': 'tanh', 'RNN_RELU': 'relu'}[self.rnn_type]
            except KeyError:
                raise ValueError("""An invalid option for `--rnn-type` was supplied,
                                 options are ['LSTM', 'GRU', 'RNN_TANH' or 'RNN_RELU']""")
            self.rnn = nn.RNN(config.num_filters, config.hidden_size, config.rnn_nlayers,
                              nonlinearity=nonlinearity, dropout=config.dropout_U)

        self.drop = nn.Dropout(config.dropout_W)
        try:
            self.num_outputs = len(config.initial_mean_value)
        except TypeError:
            self.num_outputs = 1
        self.linear = nn.Linear(config.hidden_size, self.num_outputs)

    def init_weight(self, config):
        self.embedding.weight = nn.init.xavier_uniform_(self.embedding.weight)
        # 下面weight这个是从参考资料里来的，nea里头没有的
        self.linear.weight = nn.init.xavier_uniform_(self.linear.weight)
        if not config.skip_init_bias and config.initial_mean_value > 0.:
            self.linear.bias.data.fill_((np.log(config.initial_mean_value) - np.log(1 - config.initial_mean_value)))
        else:
            self.linear.bias.data.fill_(0)

    def repackage_hidden(self, hidden):
        """
        Wraps hidden states in new Tensors, to detach them from their history.
        """
        if isinstance(hidden, torch.Tensor):
            return hidden.detach()
        else:
            return tuple(self.repackage_hidden(v) for v in hidden)

    def init_hidden(self, config, requires_grad=True):
        weight = next(self.parameters())
        if self.rnn_type == 'LSTM':
            hidden = weight.new_zeros((config.rnn_nlayers, config.batch_size, config.hidden_size),
                                      requires_grad=requires_grad)
            context = weight.new_zeros((config.rnn_nlayers, config.batch_size, config.hidden_size),
                                       requires_grad=requires_grad)
            return hidden, context
        else:
            return weight.new_zeros((config.rnn_nlayers, config.batch_size, config.hidden_size),
                                    requires_grad=requires_grad)

    def mean_over_time(self, seqs, seq_lens, batch_first=True):
        """
        :param seqs: Tensor whose size = [seq_len, batch_size, input_size] or [batch_size, seq_len, input_size]
        :param seq_lens: Tensor or list-like object describe REAL length of each sequence in `seqs`
        :param batch_first: True is seqs.size() = [batch_size, seq_len, input_size] else False
        :return: MeanOverTime value considering REAL length of sequence (or say, supporting mask-zero as Keras dose)
        """
        if batch_first:
            batch_size = seqs.size(0)
        else:
            batch_size = seqs.size(1)
            seqs = seqs.permute(1, 0, 2)
        input_size = seqs.size(-1)

        mot = torch.zeros(batch_size, input_size)
        for i in range(batch_size):
            mot[i, :] = torch.mean(seqs[i, :seq_lens[i], :], dim=0)

        return mot

    def forward(self, ipts, hidden, seq_lengths):
        x = self.embedding(ipts)
        x = x.unsqueeze(1)
        x = self.conv(x)
        x = x.squeeze(3)
        x = x.permute(0, 2, 1)
        x_packed = pack_padded_sequence(x, seq_lengths, batch_first=False,
                                        enforce_sorted=False)
        if self.rnn_type == 'LSTM':
            x_packed, _ = self.rnn(x_packed, hidden)
        else:
            x_packed, _ = self.rnn(x_packed, hidden)
        x_unpacked, lens_unpacked = pad_packed_sequence(x_packed, batch_first=False)

        x = self.drop(x_unpacked)
        x = self.mean_over_time(x, lens_unpacked, batch_first=False)  # MeanOverTime Pooling
        x = self.linear(x)
        x = torch.sigmoid(x)

        return x.squeeze()
