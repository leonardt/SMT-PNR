#!/usr/bin/env python3
import sys
import design, design.core2graph, fabric, pnr, smt
from functools import partial

import argparse
parser = argparse.ArgumentParser(description='Run place and route')
parser.add_argument('design', metavar='<DESIGN_FILE>', help='Mapped coreir file')
parser.add_argument('fabric', metavar='<FABRIC_FILE>', help='XML Fabric file')
parser.add_argument('--coreir-libs', nargs='+', help='coreir libraries to load', dest='libs', default=())
#parser.add_argument('--xml', nargs=2, metavar=('<PLACEMENT_FILE>', '<IO_FILE>'), help='output CGRA configuration in XML file with IO info')
parser.add_argument('--print', action='store_true', help='equivelent to --print-place, --print-route')
parser.add_argument('--print-place', action='store_true', dest='print_place', help='print placement information to stdout') 
parser.add_argument('--print-route', action='store_true', dest='print_route', help='print routing information to stdout')
parser.add_argument('--bitstream', metavar='<BITSTREAM_FILE>', help='output CGRA configuration in bitstream')
parser.add_argument('--annotate', metavar='<ANNOTATED_FILE>', help='output bitstream with annotations')
parser.add_argument('--solver', help='choose the smt solver to use for placement', default='Z3')
args = parser.parse_args()

design_file = args.design
fabric_file = args.fabric

print("Loading design: {}".format(design_file))
modules, nets = design.core2graph.load_core(design_file, *args.libs)
des = design.Design(modules, nets)

print("Loading fabric: {}".format(fabric_file))
fab = fabric.parse_xml(fabric_file)

p = pnr.PNR(fab, des, args.solver)

POSITION_T = partial(smt.BVXY, solver=p._place_solver)
PLACE_CONSTRAINTS = pnr.init_positions(POSITION_T), pnr.distinct, pnr.nearest_neighbor, pnr.pin_IO
PLACE_RELAXED =  pnr.init_positions(POSITION_T), pnr.distinct, pnr.pin_IO
ROUTE_CONSTRAINTS = pnr.build_msgraph, pnr.excl_constraints, pnr.reachability, pnr.dist_limit(1)
# To use multigraph encoding:
# Note: This encoding does not handle fanout for now
# Once nets represent the whole tree of connections, this will be fixed
# ROUTE_CONSTRAINTS = pnr.build_net_graphs, pnr.reachability, pnr.dist_limit(1)


print("Placing design...", end=' ')
if p.place_design(PLACE_CONSTRAINTS, pnr.place_model_reader):
    print("success!")
else:
    print("\nfailed with nearest_neighbor, relaxing...", end = ' ')
    if p.place_design(PLACE_RELAXED, pnr.place_model_reader):
        print("success!")
    else:
        print("!!!failure!!!")
        sys.exit(1)

print("Routing design...", end=' ')
if p.route_design(ROUTE_CONSTRAINTS, pnr.route_model_reader):
    print("success!")
else:
    print("!!!failure!!!")
    sys.exit(1)

if args.bitstream:
    bit_file = args.bitstream
    print("Writing bitsream to: {}".format(bit_file))
    p.write_design(pnr.write_bitstream(fabric_file, bit_file, False))

if args.annotate:
    bit_file = args.annotate
    print("Writing bitsream to: {}".format(bit_file))
    p.write_design(pnr.write_bitstream(fabric_file, bit_file, True))

    

if args.print or args.print_place:
    print("\nPlacement info:")
    p.write_design(pnr.write_debug(des))

if args.print or args.print_route:
    print("\nRouting info:")
    p.write_design(pnr.write_route_debug(des))
