"""Tree Profiling desktop app: the parts that do not depend on the user interface.

The app does not change the processing scripts in ../processes. It only calls them
(sat_docker.py, merge_sat.py, tree_info.py), shows their progress and results, and
saves / reopens results. Requirement codes in comments (NEW-4, SAVE-3 ...) refer to
the app spec.
"""
__version__ = "0.1.0"
