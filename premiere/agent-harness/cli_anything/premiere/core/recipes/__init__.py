"""Recipes: the logic of the live commands that do one job in Premiere (no click code here).

Each recipe is a function that takes a BridgeClient and plain arguments, runs one or more scripts from
core/scripts, and returns a dict. live_recipes.py wraps them in click commands.
"""
