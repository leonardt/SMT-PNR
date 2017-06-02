import lxml.etree as ET
from util import NamedIDObject
from .fabricfuns import Side, mapSide, parse_name, pos_to_side
from abc import ABCMeta


class Port(NamedIDObject):
    '''
       Represents a port on a fabric
       x         : x coordinate
       y         : y coordinate
       side      : side of tile it's on
       track     : track number (or port name for PE)
       direction : in or out (i or o)
    '''
    def __init__(self, x, y, side, track, direction='i'):
        # naming scheme is (x, y)Side_direction[track]
        super().__init__('({}, {}){}_{}[{}]'.format(x, y, side.name, direction, str(track)))
        self._x = x
        self._y = y
        self._side = side

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    @property
    def side(self):
        return self._side

    @property
    def loc(self):
        return (self._x, self._y)

    def __repr__(self):
        return self.name


class Track(NamedIDObject):
    '''
       Holds two ports describing a single track between them
       Note: only ports for inputs are described (except for ports off the edge)
             This is because output ports always map to the same input port of
             neighboring tiles thus its redundant to have both (and unnecessarily
             inflates the graph)
    '''
    def __init__(self, src, dst, width, track_names, parent):
        super().__init__('{}-{}->{}'.format(src, width, dst))
        self._src = src
        self._dst = dst
        self._width = width
        self._track_names = track_names
        self._parent = parent

    @property
    def src(self):
        return self._src

    @property
    def dst(self):
        return self._dst

    @property
    def width(self):
        return self._width

    @property
    def track_names(self):
        return self._track_names

    @property
    def parent(self):
        return self._parent


class FabricLayer:
    def __init__(self, sources, sinks, routable, tracks):
        self._sources = sources
        self._sinks = sinks
        self._routable = routable
        self._tracks = tracks

    @property
    def sources(self):
        return self._sources

    @property
    def sinks(self):
        return self._sinks

    @property
    def routable(self):
        return self._routable

    @property
    def tracks(self):
        return self._tracks


class Fabric:
    def __init__(self, parsed_params):
        self._rows = parsed_params['rows']
        self._cols = parsed_params['cols']
        self._num_tracks = min(parsed_params['num_tracks'].values())
        self._pe_locations = parsed_params['pe_locations']
        self._mem_locations = parsed_params['mem_locations']

    @property
    def rows(self):
        return self._rows

    @property
    def cols(self):
        return self._cols

    @property
    def height(self):
        ''' alias for rows'''
        return self._rows

    @property
    def width(self):
        ''' alias for cols'''
        return self._cols

    @property
    def num_tracks(self):
        return self._num_tracks

    @property
    def pe_locations(self):
        return self._pe_locations

    @property
    def mem_locations(self):
        return self._mem_locations

    def __getitem__(self, bus_width):
        return self._layers[bus_width]

    def update(self, parsed_params):
        self._layers = dict()
        for bus_width in parsed_params['bus_widths']:
            fl = FabricLayer(parsed_params['sources' + bus_width],
                             parsed_params['sinks' + bus_width],
                             parsed_params['routable' + bus_width],
                             parsed_params['tracks' + bus_width])
            self._layers[int(bus_width)] = fl


def pre_place_parse_xml(filepath):
    N = Side.N
    S = Side.S
    E = Side.E
    W = Side.W
    sides = [N, S, E, W]

    tree = ET.parse(filepath)
    root = tree.getroot()

    params = {'sides': sides}
    
    pre_process(root, params)

    return Fabric(params)


def parse_xml(filepath, fab, design, p_state):
    N = Side.N
    S = Side.S
    E = Side.E
    W = Side.W
    sides = [N, S, E, W]

    tree = ET.parse(filepath)
    root = tree.getroot()

    params = {'sides': sides}

    pre_process(root, params)

    process_regs(design, p_state)

    for bus_width in params['bus_widths']:
        params['sinks' + bus_width] = dict()
        params['sources' + bus_width] = dict()
        params['routable' + bus_width] = dict()

        SB, PE = generate_layer(bus_width, params)
        params['SB' + bus_width] = SB
        params['PE' + bus_width] = PE

        connect_tiles(bus_width, params, p_state)
        params['tracks' + bus_width] = list()

        connect_pe(root, bus_width, params)
        connect_sb(root, bus_width, params)

    return fab.update(params)


# process the registers
def process_regs(design, p_state):
    for mod in design.modules:
        if mod.resource == 'Reg':
            k = 0
            for port, net in mod.outputs.items():
                outmod = net.dst
                dst_port = net.dst_port 
                k = k+1
            assert k == 1  # should only execute loop once...

            modpos = p_state[mod][0][:-1]
            # get just the position (registers have extra info)
            outmodpos = p_state[outmod][0][0:2]

            vertport = None
            if outmod.resource == 'PE':
                # check if receiving side is a vertical port
                vertport = dst_port in {'a', 'c'}
            # take port into consideration because of vertical/horizontal track issue
            side = pos_to_side(modpos, outmodpos, vertport)
            newstate = p_state[mod][0] + (side,)
            del p_state[mod]
            p_state[mod] = newstate


def pre_process(root, params):
    rows = 0
    cols = 0
    num_tracks = dict()
    bus_widths = set()
    pe_locations = {True: set(), False: set()}
    mem_locations = set()
    for tile in root:
        # Not assuming tiles are in order
        # Although one would hope they are
        r = int(tile.get('row'))
        c = int(tile.get('col'))
        if r > rows:
            rows = r
        if c > cols:
            cols = c
        tracks = tile.get('tracks').split()
        for track in tracks:
            tr = track.split(':')
            # still indexing as x, y for now
            # i.e. col, row
            # note: removing BUS from parsed name -- kinda Hacky
            num_tracks[(c, r, tr[0][3:])] = int(tr[1])
            bus_widths.add(tr[0][3:])

        if tile.get("type") == "pe_tile_new":
            pe_locations[True].add((r, c))
        # otherwise it's a memory tile
        else:
            mem_locations.add((r, c))
            pe_locations[False].add((r, c))
            # need to get other rows that this memory tile takes up
            for sb in tile.findall('sb'):
                r_incr = int(sb.get("row")) # what to increment row by
                pe_locations[False].add((r + r_incr, c))
                # hacky but true for now: making assumption that num_tracks is the same across memory_tiles
                num_tracks[(c, r + r_incr, tr[0][3:])] = int(tr[1])

    # rows and cols should the number not the index
    params.update({'rows': rows + 1, 'cols': cols + 1, 'num_tracks': num_tracks,
                   'bus_widths': bus_widths, 'pe_locations': pe_locations,
                   'mem_locations': mem_locations})

    return True


def generate_layer(bus_width, params):
    SB = dict()
    PE = dict()
    for x in range(0, params['cols']):
        for y in range(0, params['rows']):
            PE[(x, y)] = dict()
            for side in params['sides']:
                ports = [Port(x, y, side, t, 'i') for t in range(0, params['num_tracks'][(x, y, bus_width)])]
                SB[(x, y, side, 'in')] = ports

    sources = params['sources' + bus_width]

    # add inputs from the edges as sources
    for y in range(0, params['rows']):
        # for x = 0 and all y
        for t in range(0, params['num_tracks'][(0, y, bus_width)]):
            sources[(0, y, t)] = SB[(0, y, Side.W, 'in')][t]

        # for x = cols-1 and all y
        for t in range(0, params['num_tracks'][(params['cols'] - 1, y, bus_width)]):
            sources[(params['cols']-1, y, t)] = SB[(params['cols'] - 1, y, Side.E, 'in')][t]

    for x in range(0, params['cols']):
        # for y = 0 and all x
        for t in range(0, params['num_tracks'][(x, 0, bus_width)]):
            sources[(x, 0, t)] = SB[(x, 0, Side.N, 'in')][t]

        # for y = rows-1 and all x
        for t in range(0, params['num_tracks'][(x, params['rows'] - 1, bus_width)]):
            sources[(x, params['rows']-1, t)] = SB[(x, params['rows'] - 1, Side.S, 'in')][t]

    return SB, PE


def connect_tiles(bus_width, params, p_state):
    rows = params['rows']
    cols = params['cols']
    SB = params['SB' + bus_width]
    num_tracks = params['num_tracks']
    sinks = params['sinks' + bus_width]
    sources = params['sources' + bus_width]
    routable = params['routable' + bus_width]

    for x in range(0, cols):
        for y in range(0, rows):
            for side in params['sides']:
                # Given a location and a side, mapSide returns the
                # receiving tile location and side
                adj_x, adj_y, adj_side = mapSide(x, y, side)

                # check if that switch box exists
                if (adj_x, adj_y, adj_side, 'in') in SB:
                    # make the first SB's outputs equal to
                    # the second SB's inputs
                    # i.e. no point in having redundant ports/nodes for routing
                    common_track_number = min([num_tracks[(x, y, bus_width)], num_tracks[(adj_x, adj_y, bus_width)]])
                    SB[(x, y, side, 'out')] = list()
                    for t in range(0, common_track_number):
                        potential_reg = (x, y, t, side)
                        if potential_reg in p_state.I:
                            # there's a register here. Need to split the ports
                            newport = Port(x, y, side, t, 'o')
                            SB[(x, y, side, 'out')].append(newport)
                            # add to sinks and sources
                            sinks[potential_reg] = newport
                            # index the source node from the same tile
                            sources[potential_reg] = SB[(adj_x, adj_y, adj_side, 'in')][t]
                        else:
                            SB[(x, y, side, 'out')].append(SB[(adj_x, adj_y, adj_side, 'in')][t])
                            # add port to routable
                            routable[(adj_x, adj_y, adj_side, t)] = SB[(adj_x, adj_y, adj_side, 'in')][t]
                    
                # otherwise make ports for off the edge
                else:
                    ports = []
                    for t in range(0, num_tracks[(x, y, bus_width)]):
                        p = Port(x, y, side, t, 'o')
                        ports.append(p)
                        # note sinks are indexed by edge tile location
                        # i.e. they're not really "off the edge"
                        sinks[(x, y, t)] = p

                    SB[(x, y, side, 'out')] = ports

    return True


def connect_pe(root, bus_width, params):
    PE = params['PE' + bus_width]
    SB = params['SB' + bus_width]
    tracks = params['tracks' + bus_width]
    sinks = params['sinks' + bus_width]
    sources = params['sources' + bus_width]
    for tile in root:
        y = int(tile.get('row'))
        x = int(tile.get('col'))
        # Hacky! Hardcoding the PE output port
        port = Port(x, y, Side.PE, 'out', 'o')
        PE[(x, y, 'out')] = port
        sources[(x, y, 'out')] = port
        # need to handle memory tiles differently
        if tile.get('type') == 'pe_tile_new':
            for cb in tile.findall('cb'):
                if cb.get('bus') == 'BUS' + bus_width:
                    for mux in cb.findall('mux'):
                        snk = mux.get('snk')
                        port = Port(x, y, Side.PE, snk, 'i')
                        PE[(x, y, snk)] = port
                        sinks[(x, y, snk)] = port
                        for src in mux.findall('src'):
                            port_name = src.text
                            direc, bus, side, track = parse_name(port_name)
                            srcport = SB[(x, y, side, direc)][track]
                            dstport = PE[(x, y, snk)]  # same port that was created above
                            track_names = (port_name, snk)
                            tracks.append(Track(srcport, dstport, int(bus_width), track_names, 'CB'))
        # TODO: Handle memory tiles

    return True


def connect_sb(root, bus_width, params):
    SB = params['SB' + bus_width]
    PE = params['PE' + bus_width]
    tracks = params['tracks' + bus_width]
    for tile in root:
        x = int(tile.get('row'))
        y = int(tile.get('col'))
        # need to handle memory tiles differently
        if tile.get("type") == "pe_tile_new":
            for sb in tile.findall('sb'):
                if sb.get('bus') == 'BUS' + bus_width:
                    for mux in sb.findall('mux'):
                        snk_name = mux.get('snk')
                        snk_direc, _, snk_side, snk_track = parse_name(snk_name)
                        for src in mux.findall('src'):
                            port_name = src.text
                            track_names = (port_name, snk_name)
                            dstport = SB[(x, y, snk_side, snk_direc)][snk_track]
                            # input is from PE
                            if port_name[0:2] == 'pe':
                                srcport = PE[(x, y, 'out')]
                                tracks.append(Track(srcport, dstport, int(bus_width), track_names, 'SB'))
                            # input is from another side of the SB
                            else:
                                src_direc, _, src_side, src_track = parse_name(port_name)
                                srcport = SB[(x, y, src_side, src_direc)][src_track]
                                tracks.append(Track(srcport, dstport, int(bus_width), track_names, 'SB'))
                    # now connect feedthroughs
                    for ft in sb.findall('ft'):
                        snk_name = ft.get('snk')
                        # since it's a feedthrough, there should be exactly one source
                        src_name = ft.find('src').text
                        snk_direc, _, snk_side, snk_track = parse_name(snk_name)
                        src_direc, _, src_side, src_track = parse_name(src_name)
                        track_names = (src_name, snk_name)
                        srcport = SB[(x, y, src_side, src_direc)][src_track]
                        dstport = SB[(x, y, snk_side, snk_direc)][snk_track]
                        tracks.append(Track(srcport, dstport, int(bus_width), track_names, 'SB'))

        # TODO: handle memorty tiles

    return True
