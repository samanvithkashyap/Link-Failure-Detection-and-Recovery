#!/usr/bin/env python
"""
Triangle topology for the Link Failure Detection and Recovery project.

Topology diagram
----------------

            h1
            |
            s1
           /  \\
          /    \\
        s2 ---- s3
        |        |
        h2       h3

Three OpenFlow 1.3 switches (s1, s2, s3) are wired in a triangle so that
the network has one redundant path. Each switch has one host attached.

The triangle is a physical loop on purpose: without redundancy there is
no backup path to recover onto when a link fails. The Ryu controller
runs Spanning Tree Protocol (STP) over these switches, which blocks one
of the inter-switch links at startup to break the loop and keeps it as
a hot standby.

Run with
--------
    sudo python3 topology.py

The Ryu controller must be running first on 127.0.0.1:6653:
    ryu-manager --observe-links recovery_controller.py
"""

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController


def build_triangle_topo():
    """Construct the triangle topology, wire it up, and launch the Mininet CLI."""
    net = Mininet(controller=RemoteController, switch=OVSSwitch, link=TCLink)

    # --- Controller -------------------------------------------------
    # Points Mininet at an externally-running Ryu instance on the
    # default OpenFlow port 6653. Ryu must already be running.
    print("*** Adding controller")
    c0 = net.addController(
        'c0',
        controller=RemoteController,
        ip='127.0.0.1',
        port=6653,
    )

    # --- Switches ---------------------------------------------------
    # OpenFlow 1.3 is required by our controller's match/action rules.
    print("*** Adding switches")
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    s2 = net.addSwitch('s2', protocols='OpenFlow13')
    s3 = net.addSwitch('s3', protocols='OpenFlow13')

    # --- Hosts ------------------------------------------------------
    # Fixed MAC addresses keep the demo reproducible - you can match
    # controller logs to specific hosts without chasing random MACs.
    print("*** Adding hosts")
    h1 = net.addHost('h1', mac='00:00:00:00:00:01', ip='10.0.0.1/24')
    h2 = net.addHost('h2', mac='00:00:00:00:00:02', ip='10.0.0.2/24')
    h3 = net.addHost('h3', mac='00:00:00:00:00:03', ip='10.0.0.3/24')

    # --- Links ------------------------------------------------------
    print("*** Creating links")

    # Each host sits on exactly one switch.
    net.addLink(h1, s1)
    net.addLink(h2, s2)
    net.addLink(h3, s3)

    # Triangle of inter-switch links. This is the intentional loop
    # that gives us a redundant path for STP to fall back onto.
    net.addLink(s1, s2)
    net.addLink(s2, s3)
    net.addLink(s3, s1)

    # --- Start ------------------------------------------------------
    print("*** Starting network")
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])
    s3.start([c0])

    print("*** Waiting for switches to connect to controller")
    net.waitConnected(timeout=10)

    # STP takes ~30-40 seconds to converge on a cold start (ports go
    # LISTEN -> LEARN -> FORWARD, 15 s per transition). Pings issued
    # before convergence completes will fail - wait it out.
    print("*** STP will take ~30-40 seconds to converge.")
    print("*** Try these in order at the mininet> prompt:")
    print("***   pingall")
    print("***   link s2 s3 down       -> simulate failure")
    print("***   pingall               -> observe partial/full loss during reconverge")
    print("***   pingall               -> run again after ~30 s to see full recovery")
    print("***   link s2 s3 up         -> restore original link")

    print("*** Running CLI")
    CLI(net)

    print("*** Stopping network")
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    build_triangle_topo()