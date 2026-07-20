"""Asyncio scheduler, resume, reuse (SPEC §10.5).

A node is ready when all nodes sourcing its inputs (including binding deps) are
done/reused. Ready nodes run concurrently under per-provider semaphores.
"""
