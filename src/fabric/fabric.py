import lxml.etree as ET
from util import NamedIDObject
from .fabricfuns import Side, mapSide, parse_name, pos_to_side, parse_mem_tile_name, parse_mem_sb_wire
from abc import ABCMeta
from design.module import Resource


class Port(NamedIDObject):
    '''
       Represents a port on a fabric
       x         : x coordinate
       y         : y coordinate
       resource  : Side enum for regular tracks. Resource enum for CB ports
       track     : track number (or port name for PE)
       direction : in or out (i or o)
    '''
    def __init__(self, x, y, resource, track, direction='i'):
        # naming scheme is (x, y)Side_direction[track]
        super().__init__('({}, {}){}_{}[{}]'.format(x, y, resource.name, direction, str(track)))
        self._x = x
        self._y = y
        self._resource = resource
        self._track = track
        self._inputs = set()
        self._outputs = set()

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    @property
    def side(self):
        '''
           Returns the resource assuming it's a side
        '''
        assert self._resource.name in Side.__members__
        return self._resource

    @property
    def resource(self):
        return self._resource

    @property
    def track(self):
        return self._track

    @property
    def inputs(self):
        return self._inputs

    @property
    def outputs(self):
        return self._outputs

    @property
    def loc(self):
        return (self._x, self._y)

    def split(self):
        inport = Port(self._x, self._y, self._resource, self._track, 'regi')
        inport._outputs = self._outputs
        for track in inport._outputs:
            track._src = inport
        outx, outy, outside = mapSide(self.x, self.y, self.side)
        outport = Port(outx, outy, outside, self._track, 'rego')
        outport._inputs = self._inputs
        for track in outport._inputs:
            track._dst = outport
        return outport, inport

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
    def __init__(self, src, dst, width):
        super().__init__('{}-{}->{}'.format(src, width, dst))
        self._src = src
        self._dst = dst
        self._src.outputs.add(self)
        self._dst.inputs.add(self)
        self._width = width

    @property
    def src(self):
        return self._src

    @property
    def dst(self):
        return self._dst

    @property
    def width(self):
        return self._width


class FabricLayer:
    def __init__(self, sources, sinks, ports, tracks, port_names):
        self._sources = sources
        self._sinks = sinks
        self._ports = dict()
        for port in ports:
            if port not in list(sources.values()) + list(sinks.values()):
                self._ports[(port.x, port.y, port.track, port.resource)] = port
        self._tracks = tracks
        self._port_names = port_names

    @property
    def sources(self):
        return self._sources

    @property
    def sinks(self):
        return self._sinks

    @property
    def ports(self):
        return self._ports

    @property
    def tracks(self):
        return self._tracks

    @property
    def port_names(self):
        return self._port_names


class Fabric:
    def __init__(self, parsed_params):
        self._rows = parsed_params['rows']
        self._cols = parsed_params['cols']
        self._num_tracks = min(parsed_params['num_tracks'].values())
        self._locations = dict()
        self._locations[Resource.PE] = parsed_params['pe_locations'][True]
        self._locations[Resource.Mem] = parsed_params['mem_locations']
        self._locations[Resource.Reg] = parsed_params['reg_locations'] - parsed_params['mem_locations']
        self._pe_locations = parsed_params['pe_locations']
        self._mem_locations = parsed_params['mem_locations']
        self._config = parsed_params['pnrconfig']
        self._layers = dict()
        for bus_width in parsed_params['bus_widths']:
            fl = FabricLayer(parsed_params['sources' + bus_width],
                             parsed_params['sinks' + bus_width],
                             parsed_params['ports' + bus_width],
                             parsed_params['tracks' + bus_width],
                             parsed_params['port_names' + bus_width])
            self._layers[int(bus_width)] = fl


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
    def locations(self):
        '''
            Returns a dictionary of resource type --> set of locations
        '''
        return self._locations

    # hacky returns all x==0 or y==0 locations for ios
    @property
    def io_locations(self):
        locs = set()
        for y in range(0, self.rows):
            locs.add((0, y))
        for x in range(0, self.cols):
            locs.add((x, 0))

        return locs

    @property
    def pe_locations(self):
        return self._pe_locations[True]

    @property
    def npe_locations(self):
        return self._pe_locations[False]

    @property
    def mem_locations(self):
        return self._mem_locations

    def __getitem__(self, bus_width):
        return self._layers[bus_width]

    @property
    def config(self):
        return self._config


def parse_xml(filepath, pnrconfig):
    N = Side.N
    S = Side.S
    E = Side.E
    W = Side.W
    sides = [N, S, E, W]

    tree = ET.parse(filepath)
    root = tree.getroot()

    params = {'sides': sides, 'pnrconfig': pnrconfig}

    pre_process(root, params)

    for bus_width in params['bus_widths']:
        params['sinks' + bus_width] = dict()
        params['sources' + bus_width] = dict()
        params['ports' + bus_width] = list()
        params['mem' + bus_width] = dict()

        SB, Mem = generate_layer(bus_width, params)
        params['SB' + bus_width] = SB
        params['Mem' + bus_width] = Mem
        params['PE' + bus_width] = dict()

        connect_tiles(bus_width, params)
        params['tracks' + bus_width] = list()

        connect_pe(root, bus_width, params)
        connect_memtiles_cb(root, bus_width, params)
        connect_memtiles_internal(root, bus_width, params)
        connect_sb(root, bus_width, params)

    return Fabric(params)


# process the registers
def process_regs(design, p_state, fabric):
    for mod in design.modules:
        if mod.resource == Resource.Reg:
            # could have multiple outputs, for now just taking random
            # this is heuristic anyway
            for net in mod.outputs.values():
                if net.dst in p_state:
                    outmod = net.dst
                    dst_port = net.dst_port 

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


            # now split that port
            origport = fabric[16].ports.pop(newstate)
            outport, inport = origport.split()
            # note: for now still indexing by assigned location
            fabric[16].sinks[newstate] = outport
            fabric[16].sources[newstate] = inport
            del origport


def pre_process(root, params):
    rows = 0
    cols = 0
    num_tracks = dict()
    bus_widths = set()
    pe_locations = {True: set(), False: set()}
    mem_locations = set()
    reg_locations = set()
    mem_bounds = set()
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

        if tile.get("type") is None or tile.get('type') == 'pe_tile_new':
            for sb in tile.findall('sb'):
                for mux in sb.findall('mux'):
                    if mux.get('reg') == "1":
                        # there's a register here
                        snk_name = mux.get('snk')
                        # hacky just getting last character for now
                        track = snk_name[-1:]
                        reg_locations.add((c, r, int(track)))

        if tile.get("type") == "memory_tile":
            mem_locations.add((c, r))
            pe_locations[False].add((c, r))
            # need to get other rows that this memory tile takes up
            for sb in tile.findall('sb'):
                bus = sb.get('bus')
                r_incr = int(sb.get("row")) # what to increment row by
                pe_locations[False].add((c, r + r_incr))
                # hacky but true for now: making assumption that num_tracks is the same across memory_tiles
                num_tracks[(c, r + r_incr, bus[3:])] = int(num_tracks[(c, r, bus[3:])])

                # check for registers
                for mux in sb.findall('mux'):
                    if mux.get('reg') == "1":
                        # there's a register here
                        snk_name = mux.get('snk')
                        # hacky just getting last character for now
                        track = snk_name[-1:]
                        # TODO: Fix indexing
                        # Placement can place a register at switch box in a lower
                        # nonzero memory tile row, but ports have to be indexed by
                        # memory location i.e. row = 0
                        # can fix this by modifying r in bitstream writer
                        #reg_locations.add((c, r + r_incr, int(track)))

            # data structure for holding bounds of a memory tile
            mem_bounds.add((c, r, r + r_incr))
        else:
            pe_locations[True].add((c, r))

    # rows and cols should the number not the index
    params.update({'rows': rows + 1, 'cols': cols + 1, 'num_tracks': num_tracks,
                   'bus_widths': bus_widths, 'pe_locations': pe_locations,
                   'mem_locations': mem_locations, 'mem_bounds': mem_bounds,
                   'reg_locations': reg_locations
                   })

    return True


def generate_layer(bus_width, params):
    SB = dict()
    Mem = dict()
    sources = params['sources' + bus_width]
    fabports = params['ports' + bus_width]
    # make regular switch boxes
    for loc in params['pe_locations'][True]:
        x = loc[0]
        y = loc[1]
        for side in params['sides']:
            ports = [Port(x, y, side, t, 'i') for t in range(0, params['num_tracks'][(x, y, bus_width)])]
            SB[(x, y, side, 'in')] = ports
            fabports = fabports + ports

        # add inputs from edges as sources
        if x == 0:
            for t in range(0, params['num_tracks'][(0, y, bus_width)]):
                sources[(0, y, t)] = SB[(0, y, Side.W, 'in')][t]
        if x == params['cols'] - 1:
            for t in range(0, params['num_tracks'][(params['cols'] - 1, y, bus_width)]):
                sources[(params['cols']-1, y, t)] = SB[(params['cols'] - 1, y, Side.E, 'in')][t]
        if y == 0:
            for t in range(0, params['num_tracks'][(x, 0, bus_width)]):
                sources[(x, 0, t)] = SB[(x, 0, Side.N, 'in')][t]
        if y == params['rows'] - 1:
            for t in range(0, params['num_tracks'][(x, params['rows'] - 1, bus_width)]):
                sources[(x, params['rows']-1, t)] = SB[(x, params['rows'] - 1, Side.S, 'in')][t]

    for bound in params['mem_bounds']:
        x = bound[0]
        lowery = bound[1]
        uppery = bound[2]

        # create north ports at top (i.e. lower y)
        portsN = [Port(x, lowery, Side.N, t, 'i') for t in range(0, params['num_tracks'][(x, lowery, bus_width)])]
        Mem[(x, lowery, Side.N, 'in')] = portsN

        portsS = [Port(x, lowery, Side.S, t, 'i') for t in range(0, params['num_tracks'][(x, uppery, bus_width)])]
        Mem[(x, uppery, Side.S, 'in')] = portsS

        fabports = fabports + portsN + portsS

        # create east and west ports
        # need to index by "memory location" i.e. only row=0
        for y in range(lowery, uppery + 1):
            portsW = [Port(x, lowery, Side.W, t, 'i') for t in range(0, params['num_tracks'][(x, y, bus_width)])]
            Mem[(x, y, Side.W, 'in')] = portsW
            portsE = [Port(x, lowery, Side.E, t, 'i') for t in range(0, params['num_tracks'][(x, y, bus_width)])]
            Mem[(x, y, Side.E, 'in')] = portsE
            fabports = fabports + portsW + portsE

    params['ports' + bus_width] = fabports

    return SB, Mem


def connect_tiles(bus_width, params):
    SB = params['SB' + bus_width]
    Mem = params['Mem' + bus_width]
    num_tracks = params['num_tracks']
    sinks = params['sinks' + bus_width]

    SBorMem = SB.copy()
    SBorMem.update(Mem)

    # make SB->SB and SB->Mem connections
    for loc in params['pe_locations'][True]:
        x = loc[0]
        y = loc[1]
        for side in params['sides']:
            # Given a location and a side, mapSide returns the
            # receiving tile location and side
            adj_x, adj_y, adj_side = mapSide(x, y, side)

            # check if that switch box exists
            if (adj_x, adj_y, adj_side, 'in') in SBorMem:
                # make the first SB's outputs equal to
                # the second SB's inputs
                # i.e. no point in having redundant ports/nodes for routing
                common_track_number = min([num_tracks[(x, y, bus_width)], num_tracks[(adj_x, adj_y, bus_width)]])
                SB[(x, y, side, 'out')] = SBorMem[(adj_x, adj_y, adj_side, 'in')]

            # otherwise make ports for off the edge (if not an SB or memory)
            else:
                ports = []
                for t in range(0, num_tracks[(x, y, bus_width)]):
                    p = Port(x, y, side, t, 'o')
                    ports.append(p)
                    # note sinks are indexed by edge tile location
                    # i.e. they're not really "off the edge"
                    sinks[(x, y, t)] = p

                SB[(x, y, side, 'out')] = ports

    # make Mem->SB connections and Mem->Mem (but not internal Mem->Mem) connections
    for loc in list(Mem):
        x = loc[0]
        y = loc[1]
        side = loc[2]
        adj_x, adj_y, adj_side = mapSide(x, y, side)
        if (adj_x, adj_y, adj_side, 'in') in SBorMem:
            common_track_number = min([num_tracks[(x, y, bus_width)], num_tracks[(adj_x, adj_y, bus_width)]])
            Mem[(x, y, side, 'out')] = SBorMem[(adj_x, adj_y, adj_side, 'in')]
            
        # otherwise make ports off the edge
        else:
            ports = []
            for t in range(0, num_tracks[(x, y, bus_width)]):
                p = Port(x, y, side, t, 'o')
                ports.append(p)
                # note sinks are indexed by edge tile location
                # i.e. they're not really "off the edge"
                sinks[(x, y, t)] = p

            Mem[(x, y, side, 'out')] = ports

    return True


def connect_pe(root, bus_width, params):
    PE = params['PE' + bus_width]
    SB = params['SB' + bus_width]
    tracks = params['tracks' + bus_width]
    sinks = params['sinks' + bus_width]
    sources = params['sources' + bus_width]
    pnrconfig = params['pnrconfig']
    port_names = {Resource.PE: set()}
    params['port_names' + bus_width] = port_names
    for tile in root:
        y = int(tile.get('row'))
        x = int(tile.get('col'))
        # Hacky! Hardcoding the PE output port
        port = Port(x, y, Resource.PE, 'pe_out_res', 'o')
        PE[(x, y, 'pe_out_res')] = port
        sources[(x, y, 'pe_out_res')] = port
        # need to handle memory tiles differently
        if tile.get('type') is None or tile.get('type') == 'pe_tile_new':
            for cb in tile.findall('cb'):
                if cb.get('bus') == 'BUS' + bus_width:
                    for mux in cb.findall('mux'):
                        snk = mux.get('snk')
                        port_names[Resource.PE].add(snk)
                        port = Port(x, y, Resource.PE, snk, 'i')
                        PE[(x, y, snk)] = port
                        sinks[(x, y, snk)] = port
                        for src in mux.findall('src'):
                            port_name = src.text
                            direc, bus, side, track = parse_name(port_name)
                            srcport = SB[(x, y, side, direc)][track]
                            dstport = PE[(x, y, snk)]  # same port that was created above
                            track_names = (port_name, snk)
                            track = Track(srcport, dstport, int(bus_width))
                            pnrconfig.trackconfig[track] = (track_names, 'CB')
                            tracks.append(track)

    return True


def connect_memtiles_cb(root, bus_width, params):
    SB = params['SB' + bus_width]
    Mem = params['Mem' + bus_width]
    tracks = params['tracks' + bus_width]
    sinks = params['sinks' + bus_width]
    sources = params['sources' + bus_width]
    port_names = params['port_names' + bus_width]
    pnrconfig = params['pnrconfig']
    port_names[Resource.Mem] = set()

    for tile in root:
        y = int(tile.get('row'))
        x = int(tile.get('col'))
        if tile.get('type') == 'memory_tile':
            for cb in tile.findall('cb'):
                if cb.get('bus') == 'BUS' + bus_width:
                    for mux in cb.findall('mux'):
                        snk = mux.get('snk')
                        port_names[Resource.Mem].add(snk)
                        dstport = Port(x, y, Resource.Mem, snk, 'i')
                        Mem[(x, y, snk)] = dstport
                        sinks[(x, y, snk)] = dstport
                        for src in mux.findall('src'):
                            port_name = src.text
                            # these wires should always be in_* wires
                            # and should always exist already
                            direc, bus, side, track = parse_mem_tile_name(port_name)
                            srcport = Mem[(x, y, side, direc)][track]
                            track_names = (port_name, snk)
                            track = Track(srcport, dstport, int(bus_width))
                            pnrconfig.trackconfig[track] = (track_names, 'CB')
                            tracks.append(track)

    return True


def connect_memtiles_internal(root, bus_width, params):
    Mem = params['Mem' + bus_width]
    tracks = params['tracks' + bus_width]
    fabports = params['ports' + bus_width]
    pnrconfig = params['pnrconfig']
    sources = params['sources' + bus_width]

    for tile in root:
        # memory tile can include multiple rows
        tile_y = int(tile.get('row'))
        x = int(tile.get('col'))
        if tile.get('type') == 'memory_tile':
            for sb in tile.findall('sb'):
                if sb.get('bus') == 'BUS' + bus_width:
                    row_incr = int(sb.get('row'))
                    y = tile_y + row_incr
                    for mux in sb.findall('mux'):
                        snk = mux.get('snk')

                        # get or create the snk port
                        # invariant is that snkport is assigned
                        if snk[0:3] == 'out':
                            # registers for these wires already handled i.e. split
                            direc, bus, side, track = parse_mem_tile_name(snk)
                            snkport = Mem[(x, y, side, direc)][track]
                        else:
                            # these ports are unique to the whole memory tile
                            # i.e. indexed by top location (x, tile_y, ...
                            direc, bus, side, track = parse_mem_sb_wire(snk)
                            
                            if (x, tile_y, snk, 'in') in Mem:
                                # there's no register so they should be equal
                                Mem[(x, tile_y, snk, 'out')] = Mem[(x, tile_y, snk, 'in')]
                                snkport = Mem[(x, tile_y, snk, 'out')]

                            else:
                                # make new port and set both equal to it
                                # because no register so should be equal
                                # hacky indices supposed to be different for y/tile_y
                                snkport = Port(x, tile_y, Resource.Mem, track, 'in')
                                Mem[(x, tile_y, snk, 'in')] = snkport
                                Mem[(x, tile_y, snk, 'out')] = snkport
                                fabports.append(snkport)

                        for src in mux.findall('src'):
                            src_name = src.text

                            # get or create src port
                            # invariant is that srcport is assigned
                            if src_name[0:2] == 'in':
                                direc, bus, side, track = parse_mem_tile_name(src_name)
                                srcport = Mem[(x, y, side, direc)][track]
                            else:
                                # these ports are unique to the whole memory tile
                                # i.e. indexed by top location (x, tile_y, ...
                                if (x, tile_y, src_name, 'in') in Mem:
                                    srcport = Mem[(x, tile_y, src_name, 'in')]
                                else:
                                    # create new in port if not already created
                                    srcport = Port(x, tile_y, Resource.Mem, src_name, 'in')
                                    Mem[(x, tile_y, src_name, 'in')] = srcport
                                    fabports.append(srcport)


                                # hacky: hardcoded output ports
                                # add port to sources if it's a routable signal
                                if src_name in {'valid', 'almost_full', 'mem_out'}:
                                    sources[(x, y, src_name)] = srcport

                            # make the track
                            track_names = (src_name, snk)
                            track = Track(srcport, snkport, int(bus_width))
                            pnrconfig.trackconfig[track] = (track_names, 'SB')
                            tracks.append(track)
    params['ports' + bus_width] = fabports
    return True


def connect_sb(root, bus_width, params):
    SB = params['SB' + bus_width]
    PE = params['PE' + bus_width]
    tracks = params['tracks' + bus_width]
    pnrconfig = params['pnrconfig']
    for tile in root:
        x = int(tile.get('col'))
        y = int(tile.get('row'))
        # need to handle memory tiles differently
        if tile.get("type") is None or tile.get("type") == "pe_tile_new":
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
                                srcport = PE[(x, y, 'pe_out_res')]

                            # input is from another side of the SB
                            else:
                                src_direc, _, src_side, src_track = parse_name(port_name)
                                srcport = SB[(x, y, src_side, src_direc)][src_track]

                            track = Track(srcport, dstport, int(bus_width))
                            pnrconfig.trackconfig[track] = (track_names, 'SB')
                            tracks.append(track)    

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
                        track = Track(srcport, dstport, int(bus_width))
                        pnrconfig.trackconfig[track] = (track_names, 'SB')
                        tracks.append(track)

    return True
