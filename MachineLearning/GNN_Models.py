'''
File to define Neural Networks
'''

from config import CONFIG
import torch_cluster
from torch_geometric.transforms import RadiusGraph
from torch_geometric.nn import radius_graph
from torch.nn import PairwiseDistance
import torch
from torch import nn
from torch_scatter import scatter
from torch_sparse import SparseTensor
from typing import Union, Tuple, Any, Callable, Iterator, Set, Optional, overload, TypeVar, Mapping, Dict, List
from torch.cuda.amp import autocast
import torch_geometric

T = TypeVar('T', bound='Module')

from MachineLearning.GNN_Layers import *

torch.backends.cudnn.benchmark = True


class GNN_GBNeck(torch.nn.Module):

    def __init__(self,
                 radius=0.4,
                 max_num_neighbors=32,
                 parameters=None,
                 device=None,
                 jittable=False,
                 unique_radii=None):
        '''
        GNN to reproduce the GBNeck Model
        '''
        super().__init__()

        # In order to be differentiable all tensors *need* to be created on the same device
        if device is None:
            self._device = torch.device(
                'cuda' if torch.cuda.is_available() else 'cpu')
        else:
            self._device = device

        self._nobatch = False

        # Initiate Graph Builder
        if not parameters is None:
            self._gbparameters = torch.tensor(parameters,
                                              dtype=torch.float,
                                              device=self._device)
        self._radius = radius
        self._max_num_neighbors = max_num_neighbors
        self._grapher = RadiusGraph(r=self._radius,
                                    loop=False,
                                    max_num_neighbors=self._max_num_neighbors)

        # Init Distance Calculation
        self._distancer = PairwiseDistance()
        self._jittable = jittable
        if jittable:
            self.aggregate_information = GBNeck_interaction(
                parameters, self._device,
                unique_radii=unique_radii).jittable()
            self.calculate_energies = GBNeck_energies(
                parameters, self._device,
                unique_radii=unique_radii).jittable()
        else:
            self.aggregate_information = GBNeck_interaction(
                parameters, self._device, unique_radii=unique_radii)
            self.calculate_energies = GBNeck_energies(
                parameters, self._device, unique_radii=unique_radii)

        self.lin = nn.Linear(1, 1)

    def get_edge_features(self,
                          distances,
                          alpha=2,
                          max_range=0.4,
                          min_range=0.1,
                          num_kernels=32):
        m = alpha * (max_range - min_range) / (num_kernels + 1)
        lower_bound = min_range + m
        upper_bound = max_range - m
        centers = torch.linspace(lower_bound,
                                 upper_bound,
                                 num_kernels,
                                 device=self._device)
        k = distances - centers
        return torch.maximum(torch.tensor(0, device=self._device),
                             torch.pow(1 - (k / m)**2, 3))

    def build_graph(self, data):

        # Get Radius Graph
        graph = self._grapher(data)

        # Extract edge index
        edge_index = graph.edge_index

        # Extract node features
        node_features = graph.atomic_features

        # Extract edge features
        distances = self._distancer(data.pos[edge_index[0]],
                                    data.pos[edge_index[1]])

        # For GBNeck model distances are features
        edge_attributes = distances.unsqueeze(1)

        return node_features, edge_index, edge_attributes

    def forward(self, data):

        # Enable tracking of gradients
        # Get input as Tensor create on device
        data.pos = data.pos.clone().detach().requires_grad_(True)
        lambda_electrostatics = data.atom_features[:, 7].requires_grad_(
            True).item()
        # Build Graph
        _, edge_index, edge_attributes = self.build_graph(data)

        x = self._gbparameters.repeat(torch.max(data.batch) + 1, 1)

        # Do message passing
        Bc = self.aggregate_information(
            x=x, edge_index=edge_index,
            edge_attributes=edge_attributes)  # B and charges
        energies = self.calculate_energies(x=Bc,
                                           edge_index=edge_index,
                                           edge_attributes=edge_attributes)

        # Return prediction and Gradients with respect to data
        gradients = torch.autograd.grad(energies.sum(),
                                        inputs=data.pos,
                                        create_graph=True,
                                        retain_graph=True)[0]
        forces = -1 * gradients

        if self._nobatch:
            energy = energies.sum()
            energy = energy.unsqueeze(0)
            energy = energy.unsqueeze(0)
        else:
            energy = torch.empty((torch.max(data.batch) + 1, 1),
                                 device=self._device)
            for batch in data.batch.unique():
                energy[batch] = energies[torch.where(
                    data.batch == batch)].sum()

        return energy, forces


class GNN_GBNeck_2(GNN_GBNeck):

    def forward(self, data):

        # Enable tracking of gradients
        # Get input as Tensor create on device
        data.pos = data.pos.clone().detach().requires_grad_(True)

        # Build Graph
        _, edge_index, edge_attributes = self.build_graph(data)

        x = data.atom_features

        # Do message passing
        Bc = self.aggregate_information(
            x=x, edge_index=edge_index,
            edge_attributes=edge_attributes)  # B and charges
        energies = self.calculate_energies(x=Bc,
                                           edge_index=edge_index,
                                           edge_attributes=edge_attributes)

        # Return prediction and Gradients with respect to data
        gradients = torch.autograd.grad(energies.sum(),
                                        inputs=data.pos,
                                        create_graph=True)[0]
        forces = -1 * gradients

        if self._nobatch:
            energy = energies.sum()
            energy = energy.unsqueeze(0)
            energy = energy.unsqueeze(0)
        else:
            energy = torch.empty((torch.max(data.batch) + 1, 1),
                                 device=self._device)
            for batch in data.batch.unique():
                energy[batch] = energies[torch.where(
                    data.batch == batch)].sum()

        return energy, forces

    def build_graph(self, data):

        # Get Radius Graph
        graph = self._grapher(data)

        # Extract edge index
        edge_index = graph.edge_index

        # Extract node features
        node_features = graph.atom_features

        # Extract edge features
        distances = self._distancer(data.pos[edge_index[0]],
                                    data.pos[edge_index[1]])

        # For GBNeck model distances are features
        edge_attributes = distances.unsqueeze(1)

        return node_features, edge_index, edge_attributes


class GNN_Grapher:

    def __init__(self, radius, max_num_neighbors) -> None:
        self._gnn_grapher = RadiusGraph(r=radius,
                                        loop=False,
                                        max_num_neighbors=max_num_neighbors)

    def build_gnn_graph(self, data):

        # Get Radius Graph
        graph = self._gnn_grapher(data)

        # Extract edge index
        edge_index = graph.edge_index

        # Extract node features
        node_features = graph.atomic_features

        # Extract edge features
        distances = self._distancer(data.pos[edge_index[0]],
                                    data.pos[edge_index[1]])

        # For GBNeck model distances are features
        edge_attributes = distances.unsqueeze(1)

        return node_features, edge_index, edge_attributes


class GNN_Grapher_2(GNN_Grapher):

    def build_gnn_graph(self, data):

        # Get Radius Graph
        graph = self._gnn_grapher(data)

        # Extract edge index
        edge_index = graph.edge_index

        # Extract node features
        node_features = graph.atom_features

        # Extract edge features
        distances = self._distancer(data.pos[edge_index[0]],
                                    data.pos[edge_index[1]])

        # For GBNeck model distances are features
        edge_attributes = distances.unsqueeze(1)

        return node_features, edge_index, edge_attributes


class _GNN_fix_cuda:

    _lock_device = False

    def to(self, *args, **kwargs):
        if self._lock_device:
            pass
        else:
            super().to(*args, **kwargs)


class GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA(
        GNN_GBNeck_2, GNN_Grapher_2):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=32,
                 parameters=None,
                 device=None,
                 jittable=False,
                 unique_radii=None,
                 hidden=192):

        self.gbneck_radius = 13.0
        self._gnn_radius = radius
        GNN_GBNeck_2.__init__(self,
                              radius=self.gbneck_radius,
                              max_num_neighbors=max_num_neighbors,
                              parameters=parameters,
                              device=device,
                              jittable=jittable,
                              unique_radii=unique_radii)
        GNN_Grapher_2.__init__(self,
                               radius=radius,
                               max_num_neighbors=max_num_neighbors)

        self._fraction = fraction
        if self._jittable:
            self.interaction1 = IN_layer_all_swish_2pass(
                5 + 5, hidden, radius, device, hidden).jittable()
            self.interaction2 = IN_layer_all_swish_2pass(
                hidden + hidden, 2, radius, device, hidden).jittable()
            #self.interaction3 = IN_layer_all_swish_2pass(
                #hidden + hidden, 2, radius, device, hidden).jittable()
            #self.interaction4 = IN_layer_all_swish_2pass(
                #hidden + hidden, 2, radius, device, hidden).jittable()
            #self.interaction5 = IN_layer_all_swish_2pass(
                #hidden + hidden, , radius, device, hidden).jittable()
            #self.interaction6 = IN_layer_all_swish_2pass(hidden + hidden, 2,radius,device,hidden).jittable()
        else:
            self.interaction1 = IN_layer_all_swish_2pass(
                5 + 5, hidden, radius, device, hidden)
            self.interaction2 = IN_layer_all_swish_2pass(
                hidden + hidden, 2, radius, device, hidden)
            #self.interaction3 = IN_layer_all_swish_2pass(
                #hidden + hidden, 2, radius, device, hidden)
            #self.interaction4 = IN_layer_all_swish_2pass(
                #hidden + hidden, hidden, radius, device, hidden).jittable()
            #self.interaction4 = IN_layer_all_swish_2pass(
                #hidden + hidden, 2, radius, device, hidden).jittable()
            #self.interaction6 = IN_layer_all_swish_2pass(hidden + hidden, 2,radius,device,hidden).jittable()

        self._silu = torch.nn.SiLU()
        self.sigmoid = nn.Sigmoid()

        self.sterics_ff = nn.Sequential(
            nn.Linear(1, CONFIG.sterics_hidden_dim), nn.SiLU(),
            nn.Linear(CONFIG.sterics_hidden_dim, 1), nn.Sigmoid())

        self.electrostatics_ff = nn.Sequential(
            nn.Linear(1, CONFIG.electrostatics_hidden_dim), nn.SiLU(),
            nn.Linear(CONFIG.electrostatics_hidden_dim, 1), nn.Sigmoid())
        self.gnn_params = None
        self.batch = None 
        self.gamma = torch.tensor(0.00542, device=device)  
        self.offset = torch.tensor(0.0195141, device=device)

    def forward(self,
                positions,
                lambda_sterics,
                lambda_electrostatics,
                retrieve_forces,
                jit_compile_mode: bool = True,
                batch: Optional[torch.Tensor] = None,
                atom_features: Optional[torch.Tensor] = None):
        positions.requires_grad_(True)
        lambda_sterics = lambda_sterics.to(self._device).requires_grad_(True)
        lambda_electrostatics = lambda_electrostatics.to(self._device).requires_grad_(True)
        
        
        edge_index = torch_geometric.nn.radius_graph(positions,
                                                     self.gbneck_radius, batch,
                                                     False,
                                                     self._max_num_neighbors,
                                                     'source_to_target', 1)
        gnn_edge_index = torch_geometric.nn.radius_graph(
            positions, self._gnn_radius, batch, False, self._max_num_neighbors,
            'source_to_target', 1)
        edge_attributes = self._distancer(
            positions[edge_index[0]],
            positions[edge_index[1]]).unsqueeze(1).to(torch.float32)
        gnn_edge_attributes = self._distancer(
            positions[gnn_edge_index[0]],
            positions[gnn_edge_index[1]]).unsqueeze(1).to(torch.float32)
        
        sterics_scale = self.sterics_ff(lambda_sterics.view(
            -1, 1)) * lambda_sterics.view(-1,
                                          1)  #New changes unsqueeze to view
        electrostatics_scale = self.electrostatics_ff(
            lambda_electrostatics.view(-1, 1)) * lambda_electrostatics.view(
                -1, 1)
        
        if batch is None:
            batch = torch.zeros(size=(len(self.gnn_params), )).to(torch.long)

        if self.gnn_params is not None:
            x = torch.cat([
                self.gnn_params.to(torch.float32),
                lambda_electrostatics.view(-1, 1).expand(positions.size(0), 1),
                lambda_sterics.view(-1, 1).expand(positions.size(0), 1)
                ],
                dim=-1)

        elif torch.is_tensor(atom_features):
            x = atom_features
        else:
            raise Exception("No GNN Params Given")

        x = x.float()
        
        
        l_electrostatics = electrostatics_scale[batch]
        l_sterics = sterics_scale[batch]
        # Do message passing
        Bc = self.aggregate_information(
            x=x, edge_index=edge_index,
            edge_attributes=edge_attributes)  # B and charges

        # ADD small correction
        Bcn = torch.concat((Bc, x[:, 1].unsqueeze(1), l_sterics.view(
            -1, 1), l_electrostatics.view(-1, 1)),
                           dim=1)

        #
        # x[:,7].unsqueeze(1), x[:,8].unsqueeze(1)
        Bcn = self.interaction1(edge_index=gnn_edge_index,
                                x=Bcn,
                                edge_attributes=gnn_edge_attributes)
        Bcn = self._silu(Bcn)
        Bcn = self.interaction2(edge_index=gnn_edge_index,
                                x=Bcn,
                                edge_attributes=gnn_edge_attributes)
        c_scale = Bcn[:, 0]
        sa_scale = Bcn[:, 1]

        # Calculate SA term
        radius = (x[:, 1] + self.offset).unsqueeze(1)
        sa_energies = 4.184 * self.gamma * sa_scale.unsqueeze(1) * (radius + 0.14).pow(2) * 100

        # Scale the GBNeck born radii with plus minus 50%
        Bcn = Bc[:, 0].unsqueeze(1) * (self._fraction +
                                       self.sigmoid(c_scale.unsqueeze(1)) *
                                       (1 - self._fraction) * 2)

        # get 'Born' radius with charge
        Bc = torch.concat((Bcn, Bc[:, 1].unsqueeze(1)), dim=1)

        # Evaluate GB energies
        with torch.no_grad():
            elec_energies = self.calculate_energies(x=Bc, edge_index=edge_index, edge_attributes=edge_attributes)

        # need to ensure that the energies are 0 when lambdas are 0

        # Add SA term
        energies = elec_energies * l_electrostatics + sa_energies * l_sterics
        
        grad_output: Optional[List[Optional[torch.Tensor]]] = [
            torch.ones_like(energies.sum())
        ]

        #Needed Earlier for JIT calculations
        gradients_f = torch.autograd.grad([energies.sum()],
                                          grad_outputs=grad_output,
                                          inputs=[positions],
                                          create_graph=False,
                                          retain_graph=False)[0]

        if gradients_f is not None:
            forces = torch.neg(gradients_f)
            if retrieve_forces:
                #print((energies.su, forces))
                return (energies.sum(), forces)
    

        '''
        # Return prediction and Gradients with respect to data
        gradients_sterics = torch.autograd.grad([energies.sum()],
                                                grad_outputs=grad_output,
                                                inputs=[l_sterics],
                                                create_graph=True,
                                                retain_graph=True)[0]
        gradients_electrostatics = torch.autograd.grad(
            [energies.sum()],
            grad_outputs=grad_output,
            inputs=[l_electrostatics],
            create_graph=True,
            retain_graph=True)[0]

        if self._nobatch:
            energy = energies.sum()
            energy = energy.unsqueeze(0).unsqueeze(0)
            sterics = gradients_sterics.sum()
            electrostatics = gradients_electrostatics.sum()
        else:
            if batch is not None and batch.numel() > 0:
                max_batch = int(torch.max(batch).item()) + 1
            else:
                max_batch = 1

            energy = torch.empty((max_batch, 1), device=self._device)
            sterics = torch.empty((max_batch, 1), device=self._device)
            electrostatics = torch.empty((max_batch, 1), device=self._device)

            for curr in batch.unique():
                energy[curr] = energies[torch.where(curr == batch)].sum()
                sterics[curr] = gradients_sterics.view(
                    -1, 1)[torch.where(curr == batch)].sum()
                electrostatics[curr] = gradients_electrostatics.view(
                    -1, 1)[torch.where(curr == batch)].sum()

        return energy, forces, sterics, electrostatics

        #===============================================TRAINING SECTION ======================================
        #'''


class JitGNN(

        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=32,
                 parameters=None,
                 device=None,
                 jittable=False,
                 unique_radii=None,
                 hidden=128):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, unique_radii, hidden)

    def forward(self,
                positions,
                lambda_sterics,
                lambda_electrostatics,
                vaccum,
                batch: Optional[torch.Tensor] = None,
                atom_features: Optional[torch.Tensor] = None):
        jit_compile_mode = True
        energies, forces = super().forward(
            positions,
            lambda_sterics,
            lambda_electrostatics,
            vaccum,
            jit_compile_mode,
            batch,
            atom_features,
        )
        return (energies, forces)


class GNN3_scale_128(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=32,
                 parameters=None,
                 device=None,
                 jittable=False,
                 unique_radii=None,
                 hidden=128):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, unique_radii, hidden)


class GNN3_scale_96(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=32,
                 parameters=None,
                 device=None,
                 jittable=False,
                 unique_radii=None,
                 hidden=96):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, unique_radii, hidden)


class GNN3_scale_64(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=32,
                 parameters=None,
                 device=None,
                 jittable=False,
                 unique_radii=None,
                 hidden=64):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, unique_radii, hidden)


class GNN3_scale_48(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=32,
                 parameters=None,
                 device=None,
                 jittable=False,
                 unique_radii=None,
                 hidden=48):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, unique_radii, hidden)


class GNN3_scale_32(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=32,
                 parameters=None,
                 device=None,
                 jittable=False,
                 unique_radii=None,
                 hidden=32):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, unique_radii, hidden)


class GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA_run_multiple(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA,
        _GNN_fix_cuda):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=10000,
                 parameters=None,
                 device=None,
                 jittable=False,
                 num_reps=1,
                 gbneck_radius=10.0,
                 unique_radii=None,
                 hidden=128):

        max_num_neighbors = 10000
        self._gnn_radius = radius
        GNN_GBNeck_2.__init__(self,
                              radius=gbneck_radius,
                              max_num_neighbors=max_num_neighbors,
                              parameters=parameters,
                              device=device,
                              jittable=jittable,
                              unique_radii=unique_radii)
        GNN_Grapher_2.__init__(self,
                               radius=radius,
                               max_num_neighbors=max_num_neighbors)

        self.set_num_reps(num_reps)

        self._fraction = fraction
        if self._jittable:
            self.interaction1 = IN_layer_all_swish_2pass(
                3 + 3, hidden, radius, device, hidden).jittable()
            self.interaction2 = IN_layer_all_swish_2pass(
                hidden + hidden, hidden, radius, device, hidden).jittable()
            self.interaction3 = IN_layer_all_swish_2pass(
                hidden + hidden, 2, radius, device, hidden).jittable()
        else:
            self.interaction1 = IN_layer_all_swish_2pass(
                3 + 3, hidden, radius, device, hidden)
            self.interaction2 = IN_layer_all_swish_2pass(
                hidden + hidden, hidden, radius, device, hidden)
            self.interaction3 = IN_layer_all_swish_2pass(
                hidden + hidden, 2, radius, device, hidden)

        self._silu = torch.nn.SiLU()
        self.sigmoid = nn.Sigmoid()
        self._edge_index = self.build_edge_idx(len(parameters),
                                               num_reps).to(self._device)
        self._refzero = torch.zeros(1, dtype=torch.long, device=self._device)

    def set_num_reps(self, num_reps=1):

        self._num_reps = num_reps
        self._batch = torch.zeros((num_reps * len(self._gbparameters)),
                                  dtype=torch.int64,
                                  device=self._device)
        for i in range(num_reps):
            self._batch[i * len(self._gbparameters):(i + 1) *
                        len(self._gbparameters)] = i

        self._batch_gbparameters = self._gbparameters.repeat(self._num_reps, 1)

        return 0

    def forward(self, positions):

        # Build Graph
        _, edge_index, edge_attributes = self.build_graph(positions)
        gnn_slices = edge_attributes < 0.6
        sgnn_slices = torch.squeeze(gnn_slices)
        gnn_edge_attributes = torch.unsqueeze(edge_attributes[gnn_slices], 1)
        gnn_edge_index = torch.cat(
            (torch.unsqueeze(edge_index[0][sgnn_slices], 0),
             torch.unsqueeze(edge_index[1][sgnn_slices], 0)),
            dim=0)

        # Get atom features
        x = self._batch_gbparameters

        # Do message passinge
        Bc = self.aggregate_information(
            x=x, edge_index=edge_index,
            edge_attributes=edge_attributes)  # B and charges
        # ADD small correction
        Bcn = torch.concat((Bc, x[:, 1].unsqueeze(1)), dim=1)
        Bcn = self.interaction1(edge_index=gnn_edge_index,
                                x=Bcn,
                                edge_attributes=gnn_edge_attributes)
        Bcn = self._silu(Bcn)
        Bcn = self.interaction2(edge_index=gnn_edge_index,
                                x=Bcn,
                                edge_attributes=gnn_edge_attributes)
        Bcn = self._silu(Bcn)
        Bcn = self.interaction3(edge_index=gnn_edge_index,
                                x=Bcn,
                                edge_attributes=gnn_edge_attributes)

        # Separate into polar and non-polar contributions
        c_scale = Bcn[:, 0]
        sa_scale = Bcn[:, 1]

        # Calculate SA term
        gamma = 0.00542  # kcal/(mol A^2)
        offset = 0.0195141
        radius = (x[:, 1] + offset).unsqueeze(1)
        sasa = self.sigmoid(sa_scale.unsqueeze(1)) * (radius + 0.14)**2
        sa_energies = 4.184 * gamma * sasa * 100

        # Scale the GBNeck born radii with plus minus 50%
        Bcn = Bc[:, 0].unsqueeze(1) * (self._fraction +
                                       self.sigmoid(c_scale.unsqueeze(1)) *
                                       (1 - self._fraction) * 2)

        # get 'Born' radius with charge
        Bc = torch.concat((Bcn, Bc[:, 1].unsqueeze(1)), dim=1)

        # Evaluate GB energies
        energies = self.calculate_energies(x=Bc,
                                           edge_index=edge_index,
                                           edge_attributes=edge_attributes)

        # Add SA term
        energies = energies + sa_energies

        return energies.sum()

    def build_gnn_graph(self, positions):

        # Extract edge index
        edge_index = torch_cluster.radius_graph(positions, self._gnn_radius,
                                                self._batch, False,
                                                self._max_num_neighbors,
                                                'source_to_target')

        # Extract edge features
        distances = self._distancer(positions[edge_index[0]],
                                    positions[edge_index[1]])

        # For GBNeck model distances are features
        edge_attributes = distances.unsqueeze(1)

        return None, edge_index, edge_attributes

    def build_graph(self, positions):

        edge_index = self._edge_index

        # Extract edge features
        distances = self._distancer(positions[edge_index[0]],
                                    positions[edge_index[1]])

        # For GBNeck model distances are features
        edge_attributes = distances.unsqueeze(1)

        return None, edge_index, edge_attributes

    def build_edge_idx(self, num_nodes, num_reps):

        elements_per_rep = num_nodes * (num_nodes - 1)
        edge_index = torch.zeros((2, num_reps * elements_per_rep),
                                 dtype=torch.long,
                                 device=self._device)

        for rep in range(num_reps):
            for node in range(num_nodes):
                for con in range(num_nodes):
                    if con < node:
                        edge_index[0, rep * elements_per_rep + node *
                                   (num_nodes - 1) +
                                   con] = rep * num_nodes + node
                        edge_index[1, rep * elements_per_rep + node *
                                   (num_nodes - 1) +
                                   con] = rep * num_nodes + con
                    elif con > node:
                        edge_index[0, rep * elements_per_rep + node *
                                   (num_nodes - 1) + con -
                                   1] = rep * num_nodes + node
                        edge_index[1, rep * elements_per_rep + node *
                                   (num_nodes - 1) + con -
                                   1] = rep * num_nodes + con

        return edge_index


class GNN3_scale_128_run(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA_run_multiple
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=10000,
                 parameters=None,
                 device=None,
                 jittable=False,
                 num_reps=1,
                 gbneck_radius=10,
                 unique_radii=None,
                 hidden=128):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, num_reps, gbneck_radius,
                         unique_radii, hidden)


class GNN3_scale_96_run(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA_run_multiple
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=10000,
                 parameters=None,
                 device=None,
                 jittable=False,
                 num_reps=1,
                 gbneck_radius=10,
                 unique_radii=None,
                 hidden=96):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, num_reps, gbneck_radius,
                         unique_radii, hidden)


class GNN3_scale_64_run(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA_run_multiple
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=10000,
                 parameters=None,
                 device=None,
                 jittable=False,
                 num_reps=1,
                 gbneck_radius=10,
                 unique_radii=None,
                 hidden=64):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, num_reps, gbneck_radius,
                         unique_radii, hidden)


class GNN3_scale_48_run(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA_run_multiple
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=10000,
                 parameters=None,
                 device=None,
                 jittable=False,
                 num_reps=1,
                 gbneck_radius=10,
                 unique_radii=None,
                 hidden=48):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, num_reps, gbneck_radius,
                         unique_radii, hidden)


class GNN3_scale_32_run(
        GNN3_all_swish_multiple_peptides_GBNeck_trainable_dif_graphs_corr_with_separate_SA_run_multiple
):

    def __init__(self,
                 fraction=0.5,
                 radius=0.4,
                 max_num_neighbors=10000,
                 parameters=None,
                 device=None,
                 jittable=False,
                 num_reps=1,
                 gbneck_radius=10,
                 unique_radii=None,
                 hidden=32):
        super().__init__(fraction, radius, max_num_neighbors, parameters,
                         device, jittable, num_reps, gbneck_radius,
                         unique_radii, hidden)
