"""
Framework-light building blocks shared by the four Django apps.

Nothing in here imports from ``accounts``, ``lease``, ``regulations`` or
``rental``: dependencies point inward, so the apps depend on these seams and
never the other way around.
"""
