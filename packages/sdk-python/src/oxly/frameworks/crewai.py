# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""CrewAI auto-instrumentation.

Monkey-patches CrewAI's Task and Agent classes to automatically create
spans for:
    - Task executions
    - Agent actions
    - Tool calls
    - Inter-agent communication
"""

import functools
import logging
from typing import Any

logger = logging.getLogger("oxly")

_instrumented = False


def instrument() -> None:
    """Apply CrewAI instrumentation.

    Monkey-patches CrewAI's Task and Agent classes to automatically
    create spans for all agent activities. Safe to call multiple times (idempotent).
    """
    global _instrumented
    if _instrumented:
        return

    try:
        from crewai import Agent, Task

        # Patch Task.execute
        original_task_execute = Task.execute

        @functools.wraps(original_task_execute)
        def instrumented_task_execute(self: Any, *args: Any, **kwargs: Any) -> Any:
            from oxly.context import span_context
            from oxly.tracer import Tracer

            # Create a span name, e.g. "crewai.task.write_a_post..."
            short_desc = (
                self.description[:30].replace("\n", " ").strip() + "..."
                if self.description
                else "unknown"
            )
            span_name = f"crewai.task.{short_desc}"

            tracer = Tracer.get_tracer()
            span = tracer.start_span(span_name)

            try:
                span.set_attribute("framework", "crewai")
                span.set_attribute("crewai.task.description", self.description)
                span.set_attribute(
                    "crewai.task.expected_output", getattr(self, "expected_output", "")
                )

                # Try to capture agent info if assigned
                agent = getattr(self, "agent", None)
                if agent and hasattr(agent, "role"):
                    span.set_attribute("crewai.agent.role", agent.role)

                # The context/inputs provided to the task
                context = kwargs.get("context", "")
                if context:
                    span.set_attribute("input_payload", str(context))

                with span_context(span):
                    result = original_task_execute(self, *args, **kwargs)

                # Capture final output
                if result:
                    # CrewAI tasks often return a TaskOutput object
                    output_str = getattr(result, "raw", str(result))
                    span.set_attribute("output_payload", output_str)

                span.end()
                return result
            except Exception as exc:
                span.record_exception(exc)
                span.end()
                raise

        # Patch Agent.execute_task
        original_agent_execute_task = Agent.execute_task

        @functools.wraps(original_agent_execute_task)
        def instrumented_agent_execute_task(self: Any, *args: Any, **kwargs: Any) -> Any:
            from oxly.context import span_context
            from oxly.tracer import Tracer

            role = getattr(self, "role", "unknown")
            span_name = f"crewai.agent.{role}"

            tracer = Tracer.get_tracer()
            span = tracer.start_span(span_name)

            try:
                span.set_attribute("framework", "crewai")
                span.set_attribute("crewai.agent.role", role)
                span.set_attribute("crewai.agent.goal", getattr(self, "goal", ""))
                span.set_attribute("crewai.agent.backstory", getattr(self, "backstory", ""))

                # The task being executed is usually the first arg
                task = args[0] if args else kwargs.get("task")
                if task and hasattr(task, "description"):
                    span.set_attribute("crewai.task.description", task.description)
                    span.set_attribute("input_payload", task.description)

                with span_context(span):
                    result = original_agent_execute_task(self, *args, **kwargs)

                if result:
                    span.set_attribute("output_payload", str(result))

                span.end()
                return result
            except Exception as exc:
                span.record_exception(exc)
                span.end()
                raise

        # Apply patches
        Task.execute = instrumented_task_execute
        Agent.execute_task = instrumented_agent_execute_task

        _instrumented = True
        logger.debug("CrewAI instrumentation applied successfully")

    except ImportError:
        logger.debug("CrewAI not installed, skipping instrumentation")
    except Exception:
        logger.debug("Failed to instrument CrewAI", exc_info=True)
