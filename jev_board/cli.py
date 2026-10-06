"""
jev-board: rank a GitHub Project board's open items with Jev.

    jev-board 31 --owner my-org --repos ~/code/api,~/code/web --goals goals.md
"""
import argparse


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jev-board", description=__doc__.strip().split("\n")[0])
    ap.add_argument("board", type=int, help="project number")
    ap.parse_args(argv)
