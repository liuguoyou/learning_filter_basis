import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as models

from model import common

def make_model(args, parent=False):
    return ResNet_Group(args)


class BasicBlock(nn.Module):
    def __init__(self, in_channels, out_channels, group_size, kernel_size,
                 stride=1, bias=True, conv=common.default_conv, norm=common.default_norm, act=common.default_act):
        super(BasicBlock, self).__init__()
        groups = in_channels // group_size
        modules = [conv(in_channels, in_channels, kernel_size=kernel_size, stride=stride, bias=bias, groups=groups)]
        # if norm is not None: modules.append(norm(in_channels))
        # if act is not None: modules.append(act())
        modules.append(conv(in_channels, out_channels, kernel_size=1, stride=1, bias=bias))
        # if norm is not None: modules.append(norm(out_channels))
        # if act is not None: modules.append(act())
        self.conv = nn.Sequential(*modules)

    def forward(self, x):
        return self.conv(x)


class ResBlock(nn.Module):
    def __init__(
        self, in_channels, planes, kernel_size, stride=1,
        conv3x3=common.default_conv,
        conv1x1=common.default_conv,
        norm=common.default_norm,
        act=common.default_act,
        downsample=None):

        super(ResBlock, self).__init__()
        m = [conv3x3(
            in_channels, planes, kernel_size, stride=stride, bias=False
        )]
        if norm: m.append(norm(planes))
        m.append(act())
        m.append(conv3x3(planes, planes, kernel_size, bias=False))
        if norm: m.append(norm(planes))

        self.body = nn.Sequential(*m)
        self.downsample = downsample
        self.act_out = act()

    def forward(self, x):
        out = self.body(x)
        if self.downsample is not None: x = self.downsample(x)
        # print('Out size {}; x size {}'.format(out.size(), x.size()))
        out += x
        out = self.act_out(out)

        return out

# reference: torchvision
class ResBlock_Group(nn.Module):
    def __init__(
        self, in_channels, planes, kernel_size, group_size, stride=1,
        conv3x3=BasicBlock,
        conv1x1=common.default_conv,
        norm=common.default_norm,
        act=common.default_act,
        downsample=None):

        super(ResBlock_Group, self).__init__()
        m = [conv3x3(in_channels, planes, group_size, kernel_size, stride=stride),
             nn.BatchNorm2d(planes), nn.ReLU(),
             conv3x3(planes, planes, group_size, kernel_size),
             nn.BatchNorm2d(planes)]

        self.body = nn.Sequential(*m)
        self.downsample = downsample
        self.act_out = act()

    def forward(self, x):
        out = self.body(x)
        if self.downsample is not None: x = self.downsample(x)
        # print('Out size {}; x size {}'.format(out.size(), x.size()))
        out += x
        out = self.act_out(out)

        return out

class BottleNeck(nn.Module):
    def __init__(
        self, in_channels, planes, kernel_size, stride=1,
        conv3x3=common.default_conv,
        conv1x1=common.default_conv,
        norm=common.default_norm,
        act=common.default_act,
        downsample=None):

        super(BottleNeck, self).__init__()
        m = [conv1x1(in_channels, planes, 1, bias=False)]
        if norm: m.append(norm(planes))
        m.append(act())
        m.append(conv3x3(planes, planes, kernel_size, stride=stride, bias=False))
        if norm: m.append(norm(planes))
        m.append(act())
        m.append(conv1x1(planes, 4 * planes, 1, bias=False))
        if norm: m.append(norm(4 * planes))

        self.body = nn.Sequential(*m)
        self.downsample = downsample
        self.act_out = act()

    def forward(self, x):
        out = self.body(x)
        if self.downsample is not None: x = self.downsample(x)
        out += x
        out = self.act_out(out)

        return out

class DownSampleA(nn.Module):
    def __init__(self):
        super(DownSampleA, self).__init__()

    def forward(self, x):
        # identity shortcut with 'padding zero'
        c = x.size(1) // 2
        pool = F.avg_pool2d(x, 2)
        out = F.pad(pool, (0, 0, 0, 0, c, c), 'constant', 0)

        return out

class DownSampleC(nn.Sequential):
    def __init__(
        self, in_channels, out_channels,
        stride=1, conv1x1=common.default_conv):

        m = [
            conv1x1(in_channels, out_channels, 1, stride=stride, bias=False),
            nn.BatchNorm2d(out_channels)
        ]
        super(DownSampleC, self).__init__(*m)

class ResNet_Group(nn.Module):
    def __init__(self, args, conv3x3=common.default_conv, conv1x1=common.default_conv):
        super(ResNet_Group, self).__init__()
        args = args[0]
        self.args = args
        m = []
        if args.data_train.find('CIFAR') >= 0:
            self.expansion = 1

            self.n_blocks = (args.depth - 2) // 6
            self.in_channels = 16
            self.downsample_type = 'A'
            n_classes = int(args.data_train[5:])

            kwargs = {
                'kernel_size': args.kernel_size,
                'conv3x3': conv3x3,
            }
            m.append(common.BasicBlock(args.n_colors, 16, **kwargs))
            kwargs['conv3x3'] = BasicBlock
            m.append(self.make_layer(16, self.n_blocks, **kwargs))
            m.append(self.make_layer(32, self.n_blocks, stride=2, **kwargs))
            m.append(self.make_layer(64, self.n_blocks, stride=2, **kwargs))
            m.append(nn.AvgPool2d(8))

            fc = nn.Linear(64 * self.expansion, n_classes)

        elif args.data_train == 'ImageNet':
            block_config = {
                18: ([2, 2, 2, 2], ResBlock, 1),
                34: ([3, 4, 6, 3], ResBlock, 1),
                50: ([3, 4, 6, 3], BottleNeck, 4),
                101: ([3, 4, 23, 3], BottleNeck, 4),
                152: ([3, 8, 36, 3], BottleNeck, 4)
            }
            n_blocks, self.block, self.expansion = block_config[args.depth]

            self.in_channels = 64
            self.downsample_type = 'C'
            n_classes = 1000
            kwargs = {
                'conv3x3': conv3x3,
                'conv1x1': conv1x1,
            }
            m.append(common.BasicBlock(
                args.n_colors, 64, 7, stride=2, conv3x3=conv3x3, bias=False
            ))
            m.append(nn.MaxPool2d(3, 2, padding=1))
            m.append(self.make_layer(64, n_blocks[0], 3, **kwargs))
            m.append(self.make_layer(128, n_blocks[1], 3, stride=2, **kwargs))
            m.append(self.make_layer(256, n_blocks[2], 3, stride=2, **kwargs))
            m.append(self.make_layer(512, n_blocks[3], 3, stride=2, **kwargs))
            m.append(nn.AvgPool2d(7, 1))

            fc = nn.Linear(512 * self.expansion, n_classes)

        self.features= nn.Sequential(*m)
        self.classifier = fc

        # only if when it is child model
        if conv3x3 == common.default_conv:
            if args.pretrained == 'download' or args.extend == 'download':
                state = getattr(models, 'resnet{}'.format(args.depth))(
                    pretrained=True
                )
            elif args.extend:
                state = torch.load(args.extend)
            else:
                common.init_kaiming(self)
                return

            source = state.state_dict()
            target = self.state_dict()
            for s, t in zip(source.keys(), target.keys()):
                target[t].copy_(source[s])

    def make_layer(
        self, planes, blocks, kernel_size, stride=1,
        conv3x3=BasicBlock,
        conv1x1=common.default_conv,
        bias=False):

        out_channels = planes * self.expansion
        if stride != 1 or self.in_channels != out_channels:
            if self.downsample_type == 'A':
                downsample = DownSampleA()
            elif self.downsample_type == 'C':
                downsample = DownSampleC(
                    self.in_channels,
                    out_channels,
                    stride=stride,
                    conv1x1=conv1x1
                )
        else:
            downsample = None

        kwargs = {
            'conv3x3': conv3x3,
            'conv1x1': conv1x1,
        }
        self.block = ResBlock_Group
        if planes != 64:
            kwargs['group_size'] = 4

        else:
            kwargs['group_size'] = self.args.group_size

        m = [self.block(
            self.in_channels, planes, kernel_size,
            stride=stride, downsample=downsample, **kwargs
        )]
        self.in_channels = out_channels

        for _ in range(blocks - 1):
            m.append(self.block(
                self.in_channels, planes, kernel_size, **kwargs
            ))

        return nn.Sequential(*m)

    def forward(self, x):
        x = self.features(x)
        x = self.classifier(x.squeeze())

        return x

