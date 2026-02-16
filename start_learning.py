#!/usr/bin/env python3
"""
Start the Self-Learning System
Runs continuously, learning and improving.
"""

import sys
import os

# Add memory directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'memory'))

from memory.learning_loop import start_learning, get_learning_loop

if __name__ == "__main__":
    print("=" * 60)
    print("🔄 THE DREAM TEAM - SELF-LEARNING SYSTEM")
    print("=" * 60)
    print()
    print("Starting continuous learning loop...")
    print("Press Ctrl+C to stop")
    print()

    # Start learning with 60 second intervals
    # Set max_iterations=None for infinite loop
    start_learning(interval=60, max_iterations=None)
