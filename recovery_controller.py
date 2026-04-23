"""
SDN Link Failure Detection and Recovery Controller
===================================================

Ryu application for Project #14 of the SDN Mininet Simulation assignment.

Goal
----
Detect link failures in a looped Layer-2 topology and restore connectivity
automatically by letting Spanning Tree Protocol (STP) reconverge onto a
redundant path.

How it works
------------
1. The controller inherits from Ryu's ``simple_switch_13`` so it gets a
   standard MAC-learning switch for free.
2. Ryu's ``stplib`` runs STP between the three switches. On a looped
   topology (the triangle in ``topology.py``), STP blocks one link at
   startup to break the loop and keeps it as a hot standby.
3. Bridge priorities are set per-DPID so root-bridge election is
   deterministic (s1 wins) and the demo is repeatable.
4. When an active link goes down, STP reconverges onto the blocked link.
   We listen for ``EventTopologyChange`` and flush all learned MAC-port
   mappings and flow rules on the affected switch so the new path is
   discovered cleanly by the next packet.

Run with
--------
    ryu-manager --observe-links recovery_controller.py
"""

from ryu.app import simple_switch_13
from ryu.controller.handler import MAIN_DISPATCHER, set_ev_cls
from ryu.lib import dpid as dpid_lib
from ryu.lib import stplib
from ryu.lib.packet import ethernet, packet
from ryu.ofproto import ofproto_v1_3


class LinkRecoveryController(simple_switch_13.SimpleSwitch13):
    """Learning switch with STP-driven link recovery."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # Registers stplib as a context so we can receive STP events.
    _CONTEXTS = {'stplib': stplib.Stp}

    def __init__(self, *args, **kwargs):
        super(LinkRecoveryController, self).__init__(*args, **kwargs)

        # Per-switch MAC-to-port table. Populated by packet_in, flushed
        # on topology change.
        self.mac_to_port = {}

        # Handle to the STP library so we can set bridge priorities.
        self.stp = kwargs['stplib']

        # Deterministic STP bridge priorities. Lower value wins the
        # root-bridge election, so s1 becomes root and the active/backup
        # path choice is the same on every run.
        config = {
            dpid_lib.str_to_dpid('0000000000000001'): {'bridge': {'priority': 0x8000}},
            dpid_lib.str_to_dpid('0000000000000002'): {'bridge': {'priority': 0x9000}},
            dpid_lib.str_to_dpid('0000000000000003'): {'bridge': {'priority': 0xa000}},
        }
        self.stp.set_config(config)

    # ------------------------------------------------------------------
    # Flow helpers
    # ------------------------------------------------------------------

    def delete_flow(self, datapath):
        """Remove every learned unicast flow on this switch.

        Called during a topology change so the new active path can be
        re-learned from scratch. Without this, traffic would keep being
        sent out of the now-blocked port.
        """
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        for dst in self.mac_to_port.get(datapath.id, {}).keys():
            match = parser.OFPMatch(eth_dst=dst)
            mod = parser.OFPFlowMod(
                datapath,
                command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                priority=1,
                match=match,
            )
            datapath.send_msg(mod)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @set_ev_cls(stplib.EventTopologyChange, MAIN_DISPATCHER)
    def _topology_change_handler(self, ev):
        """STP has reconverged - flush learned state so new paths are discovered.

        This is the RECOVERY half of the project. stplib fires this
        event once STP has moved ports to their new forwarding roles
        after a link event. We drop all learned MACs and installed
        flows on the affected switch; the next packet triggers a fresh
        packet_in and the new path is learned.
        """
        dp = ev.dp
        dpid_str = dpid_lib.dpid_to_str(dp.id)
        self.logger.info(
            "[dpid=%s] topology changed, clearing learned paths", dpid_str)

        if dp.id in self.mac_to_port:
            self.delete_flow(dp)
            del self.mac_to_port[dp.id]

    @set_ev_cls(stplib.EventPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """Standard L2 learning: learn source MAC, forward to known dst or flood.

        We use ``stplib.EventPacketIn`` (not the raw OpenFlow one) so
        that stplib can filter out packets arriving on ports that STP
        has blocked. Forwarding on a blocked port would re-create the
        broadcast storm STP exists to prevent.
        """
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        self.mac_to_port.setdefault(dpid, {})
        self.logger.info(
            "packet in dpid=%s src=%s dst=%s in_port=%s",
            dpid, src, dst, in_port)

        # Learn: source MAC is reachable via the port it arrived on.
        self.mac_to_port[dpid][src] = in_port

        # Forward: known destination -> unicast; unknown -> flood.
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Install a flow for known destinations so subsequent packets
        # are forwarded in hardware without hitting the controller.
        # We deliberately do NOT install a flow for floods - keeping
        # those at the controller means topology changes can redirect
        # broadcasts without leaving stale flood rules behind.
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
            self.add_flow(datapath, 1, match, actions)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)

    @set_ev_cls(stplib.EventPortStateChange, MAIN_DISPATCHER)
    def _port_state_change_handler(self, ev):
        """Log every STP state transition so the reconvergence is visible.

        This is the DETECTION half of the project. When a link goes
        down, the port transitions through DISABLE -> BLOCK -> LISTEN
        -> LEARN -> FORWARD, and each step is printed here. During
        the demo, these log lines are the evidence that the controller
        observed and responded to the failure.
        """
        dpid_str = dpid_lib.dpid_to_str(ev.dp.id)
        of_state = {
            stplib.PORT_STATE_DISABLE: 'DISABLE',
            stplib.PORT_STATE_BLOCK:   'BLOCK',
            stplib.PORT_STATE_LISTEN:  'LISTEN',
            stplib.PORT_STATE_LEARN:   'LEARN',
            stplib.PORT_STATE_FORWARD: 'FORWARD',
        }
        self.logger.info(
            "[dpid=%s][port=%d] state=%s",
            dpid_str, ev.port_no, of_state[ev.port_state])