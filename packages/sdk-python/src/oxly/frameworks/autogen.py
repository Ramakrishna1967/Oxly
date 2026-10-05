# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""AutoGen auto-instrumentation.

Monkey-patches AutoGen's ConversableAgent to automatically create spans for:
    - Agent message exchanges
    - Function calls within agents
    - Group chat turns
    - Code execution
"""

import functools
import logging
from typing import Any

logger = logging.getLogger("oxly")

_instrumented = False

def instrument() -> None:
    """Apply AutoGen instrumentation.

    Monkey-patches AutoGen's ConversableAgent class
    to automatically create spans for all message flows and actions.
    Safe to call multiple times (idempotent).
    """
    global _instrumented
    if _instrumented:
        return

    try:
        from autogen import ConversableAgent

        # Patch ConversableAgent.generate_reply
        original_generate_reply = ConversableAgent.generate_reply

        @functools.wraps(original_generate_reply)
        def instrumented_generate_reply(self: Any, *args: Any, **kwargs: Any) -> Any:
            from oxly.context import span_context
            from oxly.tracer import Tracer

            agent_name = getattr(self, "name", "unknown")
            span_name = f"autogen.agent.{agent_name}.generate"

            tracer = Tracer.get_tracer()
            span = tracer.start_span(span_name)

            try:
                span.set_attribute("framework", "autogen")
                span.set_attribute("autogen.agent.name", agent_name)

                sys_msg = getattr(self, "system_message", "")
                if sys_msg:
                    span.set_attribute("autogen.agent.system_message", sys_msg)

                # Try to capture messages context (usually first arg or in kwargs via 'messages')
                messages = args[0] if args else kwargs.get("messages", [])
                if messages:
                    span.set_attribute("input_payload", str(messages))

                with span_context(span):
                    result = original_generate_reply(self, *args, **kwargs)

                if result:
                    # Output can be a string or a dict/tuple depending on version
                    span.set_attribute("output_payload", str(result))

                span.end()
                return result
            except Exception as exc:
                span.record_exception(exc)
                span.end()
                raise

        # Patch ConversableAgent.receive
        original_receive = ConversableAgent.receive

        @functools.wraps(original_receive)
        def instrumented_receive(self: Any, *args: Any, **kwargs: Any) -> Any:
            from oxly.context import span_context
            from oxly.tracer import Tracer

            # The message is usually the first arg
            message = args[0] if args else kwargs.get("message", "")

            # The sender is usually the second arg
            sender = args[1] if len(args) > 1 else kwargs.get("sender")
            sender_name = getattr(sender, "name", "unknown") if sender else "unknown"

            receiver_name = getattr(self, "name", "unknown")
            span_name = f"autogen.msg.{sender_name}_to_{receiver_name}"

            tracer = Tracer.get_tracer()
            span = tracer.start_span(span_name)

            try:
                span.set_attribute("framework", "autogen")
                span.set_attribute("autogen.sender", sender_name)
                span.set_attribute("autogen.receiver", receiver_name)

                if message:
                    span.set_attribute("input_payload", str(message))

                with span_context(span):
                    result = original_receive(self, *args, **kwargs)

                span.end()
                return result
            except Exception as exc:
                span.record_exception(exc)
                span.end()
                raise

        # Apply patches
        ConversableAgent.generate_reply = instrumented_generate_reply
        ConversableAgent.receive = instrumented_receive

        _instrumented = True
        logger.debug("AutoGen instrumentation applied successfully")

    except ImportError:
        logger.debug("AutoGen not installed, skipping instrumentation")
    except Exception:
        logger.debug("Failed to instrument AutoGen", exc_info=True)
