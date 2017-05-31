'''
Constraint generators
'''
from functools import partial
import itertools 

from smt_switch import functions
from fabric import Side

And = functions.And()
Or = functions.Or()



def init_positions(position_type):
    '''
    init_positons:
        place initializer
    '''
    def initializer(fabric, design, state, vars, solver):
        constraints = []
        for module in design.modules_with_attr_val('fused', False):
            if module not in vars:
                p = position_type(module.name, fabric)
                vars[module] = p
                constraints.append(p.invariants)
        return And(constraints)
    return initializer

def assert_pinned(fabric, design, state, vars, solver):
    constraints = []
    for module in design.nf_modules:
        if module in state:
            pos = vars[module]
            constraints.append(pos == pos.encode(state[module][0]))
    return And(constraints)

def distinct(fabric, design, state, vars, solver):
    constraints = []
    for m1 in design.modules_with_attr_val('fused', False):
        for m2 in design.modules_with_attr_val('fused', False):
            if m1 != m2 and m1.resource == m2.resource:
                v1,v2 = vars[m1],vars[m2]
                c = v1.flat != v2.flat

                if m1.resource == 'Reg':
                    constraints.append(Or(c,  v1.c != v2.c))
                else:
                    constraints.append(c)

    return And(constraints)

def register_colors(fabric, design, state, vars, solver):
    constraints = []
    for net in design.virtual_nets:
        src = net.src
        dst = net.dst
        if src.resource == dst.resource == 'Reg':
            constraints.append(vars[src].c == vars[dst].c)
    return And(constraints)

def nearest_neighbor(fabric, design, state, vars, solver):
    dxdy = ((0,1), (1,0))
    return _neighborhood(dxdy, fabric, design, state, vars, solver)


def neighborhood(dist): 
    dxdy = ((x,y) for x,y in itertools.product(range(dist+1), repeat=2) if x+y <= dist and x+y > 0)
    return partial(_neighborhood, dxdy)

def _neighborhood(dxdy, fabric, design, state, vars, solver):
    constraints = []
    for net in design.virtual_nets:
        src = net.src
        dst = net.dst
        c = []
        dx = vars[src].delta_x_fun(vars[dst])
        dy = vars[src].delta_y_fun(vars[dst])
        #c.append(And(dx(0), dy(0)))
        for x, y in dxdy:
            c.append(And(dx(x), dy(y)))
        constraints.append(Or(c))


    return And(constraints)
        

def pin_IO(fabric, design, state, vars, solver):
    constraints = []
    for module in design.modules_with_attr_val('type_', 'IO'):
        pos = vars[module]
        c = [pos.x == pos.encode_x(0),
             pos.y == pos.encode_y(0)]
        constraints.append(Or(c))
    return And(constraints)



#################################### Routing Constraints ################################

def excl_constraints(fabric, design, p_state, r_state, vars, solver, layer=16):
    '''
        Exclusivity constraints for single graph encoding
        Works with build_msgraph, reachability and dist_limit
    '''
    c = []
    graph = solver.graphs[0]
    # TODO: don't hardcode these -- get from coreir?
    ports = {'a', 'b'}

    sources = fabric[layer].sources
    sinks = fabric[layer].sinks

    # for connected modules, make sure it's not connected to wrong inputs
    for net in design.virtual_nets:
        src = net.src
        dst = net.dst
        dst_port = net.dst_port
        src_pos = p_state[src][0]
        dst_pos = p_state[dst][0]

        # find correct index tuple (based on resource type)
        src_index = src_pos
        if src.resource == 'PE':
            src_index = src_index + (net.src_port,)

        # TODO: Fix this so doesn't assume only connected to one input port
        # there might be weird cases where you want to drive multiple inputs
        # of dst module with one output
        if dst.resource == 'PE':
            for port in ports - set(dst_port):
                c.append(~vars[net].reaches(vars[sources[src_index]],
                                            vars[sinks[dst_pos + (port,)]]))
        # if not a PE, then there aren't other ports -- do nothing

        
    # make sure modules that aren't connected are not connected
    for m1 in design.modules_with_attr_val('fused', False):
        inputs = {x.src for x in m1.inputs.values()}
        contracted_inputs = set()
        for src in inputs:
            if src.fused:
                assert len(src.inputs) <= 1
                if src.inputs:
                    srcnet = next(iter(src.inputs.values()))
                    src = srcnet.src
                else:
                    continue
            # add the (potentially contracted) src
            contracted_inputs.add(src)
        m1_pos = p_state[m1][0]
        for m2 in design.modules_with_attr_val('fused', False):
            if m2 != m1 and m2 not in contracted_inputs:
                m2_pos = p_state[m2][0]
                m2_index = m2_pos
                if m2.resource == 'PE':
                    m2_index = m2_index + ('out',)

                if m1.resource == 'PE':
                    for port in ports:
                        c.append(~graph.reaches(vars[sources[m2_index]],
                                                vars[sinks[m1_pos + (port,)]]))
                else:
                    c.append(~graph.reaches(vars[sources[m2_index]],
                                            vars[sinks[m1_pos]]))

    return solver.And(c)


def reachability(fabric, design, p_state, r_state, vars, solver, layer=16):
    '''
        Enforce reachability for nets in single graph encoding
        Works with build_msgraph, excl_constraints and dist_limit
    '''
    reaches = []
    sources = fabric[layer].sources
    sinks = fabric[layer].sinks
    for net in design.virtual_nets:
        src = net.src
        dst = net.dst
        src_port = net.src_port
        dst_port = net.dst_port
        src_index = p_state[src][0]
        dst_index = p_state[dst][0]

        # get index tuple (if it's a PE, need to append port)
        if src.resource == 'PE':
            src_index = src_index + (src_port,)
        if dst.resource == 'PE':
            dst_index = dst_index + (dst_port,)

        reaches.append(vars[net].reaches(vars[sources[src_index]],
                                         vars[sinks[dst_index]]))

    return solver.And(reaches)


# TODO: Fix indexing for distance constraints
def dist_limit(dist_factor):
    '''
       Enforce a global distance constraint. Works with single or multi graph encoding
       dist_factor is intepreted as manhattan distance on a placement grid
       (i.e. distance between adjacent PEs is 1)
    '''
    if not isinstance(dist_factor, int):
        raise ValueError('Expected integer distance factor. Received {}'.format(type(dist_factor)))

    def dist_constraints(fabric, design, p_state, r_state, vars, solver, layer=16):
        constraints = []
        sources = fabric[layer].sources
        sinks = fabric[layer].sinks
        for net in design.virtual_nets:
            src = net.src
            dst = net.dst
            src_port = net.src_port
            dst_port = net.dst_port
            src_pos = p_state[src][0]
            dst_pos = p_state[dst][0]

            # get correct index (based on resource type)
            src_index = src_pos
            dst_index = dst_pos
            if src.resource == 'PE':
                src_index = src_index + (src_port,)
            if dst.resource == 'PE':
                dst_index = dst_index + (dst_port,)

            manhattan_dist = int(abs(src_pos[0] - dst_pos[0]) + abs(src_pos[1] - dst_pos[1]))
            # This is just a weird heuristic for now. We have to have at least 2*manhattan_dist because
            # for each jump it needs to go through a port. So 1 in manhattan distance is 2 in monosat distance
            # Additionally, because the way ports are connected (i.e. only accessible from horizontal or vertical tracks)
            # It often happens that a routing is UNSAT for just 2*manhattan_dist so instead we use a factor of 3 and add 1
            # You can adjust it with dist_factor
            heuristic_dist = 3*dist_factor*manhattan_dist + 1

            # if there are registers, this allows up to double the needed length
            constraints.append(vars[net].distance_leq(vars[sources[src_index]],
                                                      vars[sinks[dst_index]],
                                                      heuristic_dist))

        return solver.And(constraints)
    return dist_constraints

# TODO: Fix node generation. --might be fine already?
def build_msgraph(fabric, design, p_state, r_state, vars, solver, layer=16):
    # to comply with multigraph, add graph for each net
    # note: in this case, all point to the same graph
    # this allows us to reuse constraints such as dist_limit and use the same model_reader
    solver.add_graph()
    for net in design.virtual_nets:
        vars[net] = solver.graphs[0]

    graph = solver.graphs[0]  # only one graph in this encoding

    sources = fabric[layer].sources
    sinks = fabric[layer].sinks

    # add msnodes for all the used PEs first (because special naming scheme)
    # Hacky! Hardcoding port names
    for x in range(fabric.width):
        for y in range(fabric.height):
            if (x, y) in p_state.I:
                vars[sinks[(x, y, 'a')]] = graph.addNode('({},{})PE_a'.format(x, y))
                vars[sinks[(x, y, 'b')]] = graph.addNode('({},{})PE_b'.format(x, y))
                vars[sources[(x, y, 'out')]] = graph.addNode('({},{})PE_out'.format(x, y))

    for track in fabric[layer].tracks:
        src = track.src
        dst = track.dst
        # naming scheme is (x, y)Side_direction[track]
        if src.side == Side.PE and (src.x, src.y) not in p_state.I:
            continue
        if src not in vars:
            vars[src] = graph.addNode(src.name)
        if dst not in vars:
            vars[dst] = graph.addNode(dst.name)

        # create a monosat edge
        e = graph.addEdge(vars[src], vars[dst])
        vars[e] = track  # we need to recover the track in model_reader

    return solver.And([])


def build_net_graphs(fabric, design, p_state, r_state, vars, solver, layer=16):
    '''
        An alternative monosat encoding which builds a graph for each net.
        Handles exclusivity constraints inherently
    '''

    # NOTE: Currently broken for fanout
    # Making nets contain whole tree of connections will fix this issue

    # NOTE 2: Also broken by new unplaceable module changes. Nets don't
    # correspond to layers any more (or at least until the nets are the whole tree)

    # create graphs for each net
    node_dict = dict()  # used to keep track of nodes in each graph
    for net in design.virtual_nets:
        vars[net] = solver.add_graph()
        node_dict[net] = dict()

    sources = fabric[layer].sources
    sinks = fabric[layer].sinks

    # add msnodes for all the used PEs first (because special naming scheme)
    for x in range(fabric.width):
        for y in range(fabric.height):
            if (x, y) in p_state.I:
                for net in design.virtual_nets:
                    src = net.src
                    dst = net.dst
                    # currently broken because have two nets for one connection
                    # if there's an unplaceable node
                    node_dict[net][a] = solver.false()
                    node_dict[net][b] = solver.false()
                    node_dict[net][out] = solver.false()
                # add each node to vars as well
                # only need to add it once (not for each net/graph)
                vars[sinks[(x, y, 'a')]] = a
                vars[sinks[(x, y, 'b')]] = b
                vars[sources[(x, y, 'out')]] = out

    for track in fabric[layer].tracks:
        src = track.src
        dst = track.dst

        # naming scheme is (x, y)Side_direction[track]
        if src not in vars:
            for net in design.virtual_nets:
                u = vars[net].addNode(src.name)
                node_dict[net][u] = solver.false()
            vars[src] = u
        if dst not in vars:
            for net in design.virtual_nets:
                v = vars[net].addNode(dst.name)
                node_dict[net][v] = solver.false()
            vars[dst] = v

        # keep track of whether a node is 'active' based on connected edges
        for net in design.virtual_nets:
            src = net.src
            dst = net.dst
            e = vars[net].addEdge(vars[src], vars[dst])
            node_dict[net][vars[src]] = solver.Or(node_dict[net][vars[src]], e)
            node_dict[net][vars[dst]] = solver.Or(node_dict[net][vars[dst]], e)
            # put in r_state because we need to map track to multiple edges
            # i.e. need BiMultiDict
            # Plus, we only use this in model_reader so it makes sense to have in r_state
            vars[e] = track

    # now enforce that each node is only used in one of the graphs
    # Note: all graphs have same nodes, so can get them from any graph
    for node in range(0, solver.graphs[0].nodes):
        node_in_graphs = [node_dict[net][node] for net in design.virtual_nets]
        solver.AssertAtMostOne(node_in_graphs)

    return solver.And([])
