"""
Consensus tool - Step-by-step multi-model consensus with expert analysis

This tool provides a structured workflow for gathering consensus from multiple models.
It guides the CLI agent through systematic steps where the CLI agent first provides its own analysis,
then consults each requested model one by one, and finally synthesizes all perspectives.

Key features:
- Step-by-step consensus workflow with progress tracking
- The CLI agent's initial neutral analysis followed by model-specific consultations
- Context-aware file embedding
- Support for stance-based analysis (for/against/neutral)
- Final synthesis combining all perspectives
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import Field, model_validator

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from mcp.types import TextContent

from config import TEMPERATURE_ANALYTICAL
from systemprompts import CONSENSUS_PROMPT
from tools.shared.base_models import ConsolidatedFindings, WorkflowRequest
from tools.shared.exceptions import ToolExecutionError
from utils.conversation_context import continuation_offer, current_invocation
from utils.conversation_memory import (
    ConversationMemoryError,
    ConversationNotFoundError,
    get_thread,
    memory_persistence_warning,
)
from utils.model_context import get_request_budget

from .workflow.base import WorkflowTool

logger = logging.getLogger(__name__)

# Tool-specific field descriptions for consensus workflow
CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS = {
    "step": (
        "Consensus prompt. Step 1: write the exact proposal/question every model will see (use 'Evaluate…', not meta commentary). "
        "Steps 2+: capture internal notes about the latest model response—these notes are NOT sent to other models."
    ),
    "step_number": "Current step index (starts at 1). Step 1 is your analysis; steps 2+ handle each model response.",
    "total_steps": "Total steps = number of models consulted plus the final synthesis step.",
    "next_step_required": "True if more model consultations remain; set false when ready to synthesize.",
    "findings": (
        "Step 1: your independent analysis for later synthesis (not shared with other models). Steps 2+: summarize the newest model response."
    ),
    "relevant_files": "Optional supporting files that help the consensus analysis. Must be absolute full, non-abbreviated paths.",
    "models": (
        "User-specified list of models to consult (provide at least two entries). "
        "Each entry may include model, stance (for/against/neutral), and stance_prompt. "
        "Each (model, stance) pair must be unique, e.g. [{'model':'gpt5','stance':'for'}, {'model':'pro','stance':'against'}]."
    ),
    "current_model_index": "0-based index of the next model to consult (managed internally).",
    "model_responses": "Internal log of responses gathered so far.",
    "images": "Optional absolute image paths or base64 references that add helpful visual context.",
}


class ConsensusRequest(WorkflowRequest):
    """Request model for consensus workflow steps"""

    # Required fields for each step
    step: str = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["step"])
    step_number: int = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["step_number"])
    total_steps: int = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["total_steps"])
    next_step_required: bool = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["next_step_required"])

    # Investigation tracking fields
    findings: str = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["findings"])
    confidence: str = Field(default="exploring", exclude=True, description="Not used")

    # Consensus-specific fields (only needed in step 1)
    models: list[dict] | None = Field(None, description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["models"])
    relevant_files: list[str] | None = Field(
        default_factory=list,
        description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["relevant_files"],
    )

    # Internal tracking fields
    current_model_index: int | None = Field(
        0,
        description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["current_model_index"],
    )
    model_responses: list[dict] | None = Field(
        default_factory=list,
        description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["model_responses"],
    )

    # Optional images for visual debugging
    images: list[str] | None = Field(default=None, description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["images"])

    # Override inherited fields to exclude them from schema
    temperature: float | None = Field(default=None, exclude=True)
    thinking_mode: str | None = Field(default=None, exclude=True)

    # Not used in consensus workflow
    files_checked: list[str] | None = Field(default_factory=list, exclude=True)
    relevant_context: list[str] | None = Field(default_factory=list, exclude=True)
    issues_found: list[dict] | None = Field(default_factory=list, exclude=True)
    hypothesis: str | None = Field(None, exclude=True)

    @model_validator(mode="after")
    def validate_step_one_requirements(self):
        """Ensure step 1 has required models field and unique model+stance combinations."""
        if self.step_number == 1:
            if not self.models:
                raise ValueError("Step 1 requires 'models' field to specify which models to consult")

            # Check for unique model + stance combinations
            seen_combinations = set()
            for model_config in self.models:
                model_name = model_config.get("model", "")
                stance = model_config.get("stance", "neutral")
                combination = f"{model_name}:{stance}"

                if combination in seen_combinations:
                    raise ValueError(
                        f"Duplicate model + stance combination found: {model_name} with stance '{stance}'. "
                        f"Each model + stance combination must be unique."
                    )
                seen_combinations.add(combination)

        return self


class ConsensusTool(WorkflowTool):
    """
    Consensus workflow tool for step-by-step multi-model consensus gathering.

    This tool implements a structured consensus workflow where the CLI agent first provides
    its own neutral analysis, then consults each specified model individually,
    and finally synthesizes all perspectives into a unified recommendation.
    """

    workflow_state_defaults = {
        "original_proposal": None,
        "initial_prompt": None,
        "models_to_consult": [],
        "accumulated_responses": [],
    }

    def __init__(self):
        super().__init__()
        self.initial_prompt: str | None = None
        self.original_proposal: str | None = None  # Store the original proposal separately
        self.models_to_consult: list[dict] = []
        self.accumulated_responses: list[dict] = []
        self._current_arguments: dict[str, Any] = {}

    def get_name(self) -> str:
        return "consensus"

    def get_description(self) -> str:
        return (
            "Builds multi-model consensus through systematic analysis and structured debate. "
            "Use for complex decisions, architectural choices, feature proposals, and technology evaluations. "
            "Consults multiple models with different stances to synthesize comprehensive recommendations."
        )

    def get_system_prompt(self) -> str:
        # For the CLI agent's initial analysis, use a neutral version of the consensus prompt
        return CONSENSUS_PROMPT.replace(
            "{stance_prompt}",
            """BALANCED PERSPECTIVE

Provide objective analysis considering both positive and negative aspects. However, if there is overwhelming evidence
that the proposal clearly leans toward being exceptionally good or particularly problematic, you MUST accurately
reflect this reality. Being "balanced" means being truthful about the weight of evidence, not artificially creating
50/50 splits when the reality is 90/10.

Your analysis should:
- Present all significant pros and cons discovered
- Weight them according to actual impact and likelihood
- If evidence strongly favors one conclusion, clearly state this
- Provide proportional coverage based on the strength of arguments
- Help the questioner see the true balance of considerations

Remember: Artificial balance that misrepresents reality is not helpful. True balance means accurate representation
of the evidence, even when it strongly points in one direction.""",
        )

    def get_default_temperature(self) -> float:
        return TEMPERATURE_ANALYTICAL

    def get_model_category(self) -> ToolModelCategory:
        """Consensus workflow requires extended reasoning"""
        from tools.models import ToolModelCategory

        return ToolModelCategory.EXTENDED_REASONING

    def get_workflow_request_model(self):
        """Return the consensus workflow-specific request model."""
        return ConsensusRequest

    def get_input_schema(self) -> dict[str, Any]:
        """Generate input schema for consensus workflow."""
        from .workflow.schema_builders import WorkflowSchemaBuilder

        # Consensus tool-specific field definitions
        consensus_field_overrides = {
            # Override standard workflow fields that need consensus-specific descriptions
            "step": {
                "type": "string",
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["step"],
            },
            "step_number": {
                "type": "integer",
                "minimum": 1,
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["step_number"],
            },
            "total_steps": {
                "type": "integer",
                "minimum": 1,
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["total_steps"],
            },
            "next_step_required": {
                "type": "boolean",
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["next_step_required"],
            },
            "findings": {
                "type": "string",
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["findings"],
            },
            "relevant_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["relevant_files"],
            },
            # consensus-specific fields (not in base workflow)
            "models": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "stance": {"type": "string", "enum": ["for", "against", "neutral"], "default": "neutral"},
                        "stance_prompt": {"type": "string"},
                    },
                    "required": ["model"],
                },
                "description": (
                    "User-specified roster of models to consult (provide at least two entries). "
                    + CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["models"]
                ),
                "minItems": 2,
            },
            "current_model_index": {
                "type": "integer",
                "minimum": 0,
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["current_model_index"],
            },
            "model_responses": {
                "type": "array",
                "items": {"type": "object"},
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["model_responses"],
            },
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["images"],
            },
        }

        # Provide guidance on available models similar to single-model tools
        model_description = (
            "When the user names a model, you MUST use that exact value or report the "
            "provider error—never swap in another option. Use the `listmodels` tool for the full roster."
        )

        summaries, total, restricted = self._get_ranked_model_summaries()
        remainder = max(0, total - len(summaries))
        if summaries:
            label = "Allowed models" if restricted else "Top models"
            top_line = "; ".join(summaries)
            if remainder > 0:
                top_line = f"{label}: {top_line}; +{remainder} more via `listmodels`."
            else:
                top_line = f"{label}: {top_line}."
            model_description = f"{model_description} {top_line}"
        else:
            model_description = (
                f"{model_description} No models detected—configure provider credentials or use the `listmodels` tool "
                "to inspect availability."
            )

        restriction_note = self._get_restriction_note()
        if restriction_note and (remainder > 0 or not summaries):
            model_description = f"{model_description} {restriction_note}."

        existing_models_desc = consensus_field_overrides["models"]["description"]
        consensus_field_overrides["models"]["description"] = f"{existing_models_desc} {model_description}"

        # Define excluded fields for consensus workflow
        excluded_workflow_fields = [
            "files_checked",  # Not used in consensus workflow
            "relevant_context",  # Not used in consensus workflow
            "issues_found",  # Not used in consensus workflow
            "hypothesis",  # Not used in consensus workflow
            "confidence",  # Not used in consensus workflow
        ]

        excluded_common_fields = [
            "model",  # Consensus uses 'models' field instead
            "temperature",  # Not used in consensus workflow
            "thinking_mode",  # Not used in consensus workflow
        ]

        requires_model = self.requires_model()
        model_field_schema = self.get_model_field_schema() if requires_model else None
        auto_mode = self.is_effective_auto_mode() if requires_model else False

        return WorkflowSchemaBuilder.build_schema(
            tool_specific_fields=consensus_field_overrides,
            model_field_schema=model_field_schema,
            auto_mode=auto_mode,
            tool_name=self.get_name(),
            excluded_workflow_fields=excluded_workflow_fields,
            excluded_common_fields=excluded_common_fields,
            require_model=requires_model,
        )

    def get_required_actions(
        self, step_number: int, confidence: str, findings: str, total_steps: int, request=None
    ) -> list[str]:  # noqa: ARG002
        """Define required actions for each consensus phase.

        Now includes request parameter for continuation-aware decisions.
        Note: confidence parameter is kept for compatibility with base class but not used.
        """
        if step_number == 1:
            # CLI Agent's initial analysis
            return [
                "You've provided your initial analysis. The tool will now consult other models.",
                "Wait for the next step to receive the first model's response.",
            ]
        elif step_number < total_steps - 1:
            # Processing individual model responses
            return [
                "Review the model response provided in this step",
                "Note key agreements and disagreements with previous analyses",
                "Wait for the next model's response",
            ]
        else:
            # Ready for final synthesis
            return [
                "All models have been consulted",
                "Synthesize all perspectives into a comprehensive recommendation",
                "Identify key points of agreement and disagreement",
                "Provide clear, actionable guidance based on the consensus",
            ]

    def should_call_expert_analysis(self, consolidated_findings, request=None) -> bool:
        """Consensus workflow doesn't use traditional expert analysis - it consults models step by step."""
        return False

    def prepare_expert_analysis_context(self, consolidated_findings) -> str:
        """Not used in consensus workflow."""
        return ""

    def requires_expert_analysis(self) -> bool:
        """Consensus workflow handles its own model consultations."""
        return False

    def requires_model(self) -> bool:
        """
        Consensus tool doesn't require model resolution at the MCP boundary.

        Uses it's own set of models

        Returns:
            bool: False
        """
        return False

    # Hook method overrides for consensus-specific behavior

    def prepare_step_data(self, request) -> dict:
        """Prepare consensus-specific step data."""
        step_data = {
            "step": request.step,
            "step_number": request.step_number,
            "findings": request.findings,
            "files_checked": [],  # Not used
            "relevant_files": request.relevant_files or [],
            "relevant_context": [],  # Not used
            "issues_found": [],  # Not used
            "confidence": "exploring",  # Not used, kept for compatibility
            "hypothesis": None,  # Not used
            "images": request.images or [],  # Now used for visual context
        }
        return step_data

    def _build_consensus_summary(self) -> dict[str, Any]:
        """Summarize the latest outcome for each requested model/stance pair."""
        outcomes = {(r["model"], r.get("stance", "neutral")): r for r in self.accumulated_responses}
        participants = [outcomes.get((m["model"], m.get("stance", "neutral"))) for m in self.models_to_consult]
        successful = [r for r in participants if r and r.get("status") == "success"]
        failed = [r for r in participants if r and r.get("status") != "success"]
        pending = sum(r is None for r in participants)
        providers = sorted({r["metadata"]["provider"] for r in successful if r.get("metadata", {}).get("provider")})
        status = "complete" if successful and not failed and not pending else "partial" if successful else "failed"
        return {
            "initial_prompt": self.original_proposal if self.original_proposal else self.initial_prompt,
            "status": status,
            "requested_models": len(self.models_to_consult),
            "successful_models": len(successful),
            "failed_models": len(failed),
            "pending_models": pending,
            "models_consulted": [f"{r['model']}:{r.get('stance', 'neutral')}" for r in successful],
            "total_responses": len(successful),
            "successful_providers": providers,
            "successful_provider_count": len(providers),
            "participant_errors": [
                {
                    "model": r["model"],
                    "stance": r.get("stance", "neutral"),
                    "error": r.get("error", "Consultation failed"),
                }
                for r in failed
            ],
            # Finishing consultations does not establish agreement or confidence.
            "consensus_confidence": None,
        }

    def _complete_consensus(self, response_data: dict) -> dict:
        """Apply the same outcome and synthesis instructions on both completion paths."""
        summary = self._build_consensus_summary()
        status = summary["status"]
        response_data["complete_consensus"] = summary
        response_data["consensus_complete"] = status == "complete"
        response_data["status"] = f"consensus_workflow_{status}"
        response_data["next_step_required"] = False
        if status == "failed":
            response_data["next_steps"] = (
                "CONSENSUS FAILED: no successful model responses are available. Report the participant errors "
                "and any unconsulted models. Do not claim a consensus or infer model agreement. "
                "Resolve the failures before requesting another consultation."
            )
        else:
            prefix = (
                "CONSULTATIONS COMPLETE. "
                if status == "complete"
                else "PARTIAL CONSENSUS: coverage is incomplete. Report failed or unconsulted participants and "
                "qualify any recommendation; do not claim full or high-confidence consensus. "
            )
            response_data["next_steps"] = prefix + (
                "Synthesize only successful perspectives: identify agreement and disagreement, explain the "
                "evidence for your recommendation, and present actionable next steps and remaining risks."
            )
        return response_data

    async def handle_work_completion(self, response_data: dict, request, arguments: dict) -> dict:  # noqa: ARG002
        """Handle final synthesis without inferring consensus from attempted calls."""
        return self._complete_consensus(response_data)

    def handle_work_continuation(self, response_data: dict, request) -> dict:
        """Handle continuation between consensus steps."""
        current_idx = request.current_model_index or 0

        if request.step_number == 1:
            # After CLI Agent's initial analysis, prepare to consult first model
            response_data["status"] = "consulting_models"
            response_data["next_model"] = self.models_to_consult[0] if self.models_to_consult else None
            response_data["next_steps"] = (
                "Your initial analysis is complete. The tool will now consult the specified models."
            )
        elif current_idx < len(self.models_to_consult):
            next_model = self.models_to_consult[current_idx]
            response_data["status"] = "consulting_next_model"
            response_data["next_model"] = next_model
            response_data["models_remaining"] = len(self.models_to_consult) - current_idx
            response_data["next_steps"] = f"Model consultation in progress. Next: {next_model['model']}"
        else:
            response_data["status"] = "ready_for_synthesis"
            response_data["next_steps"] = "All models consulted. Ready for final synthesis."

        return response_data

    async def execute_workflow(self, arguments: dict[str, Any]) -> list:
        """Override execute_workflow to handle model consultations between steps."""

        # Store arguments
        self._attempted_model_name = None
        self._attempted_provider_name = None
        self._current_arguments = arguments

        # Validate request
        request = self.get_workflow_request_model()(**arguments)
        self.restore_workflow_state(request)

        invocation = current_invocation(self)
        continuation_id = invocation.thread_id if invocation else request.continuation_id
        if continuation_id and get_thread(continuation_id) is None:
            raise ConversationNotFoundError("Conversation thread was not found or has expired")
        request.continuation_id = continuation_id

        if request.step_number == 1:
            self.work_history = []
            self.consolidated_findings = ConsolidatedFindings()
            # Store the original proposal from step 1 - this is what all models should see
            self.store_initial_issue(request.step)
            self.initial_request = request.step
            self.models_to_consult = request.models or []
            self.accumulated_responses = []
            # Set total steps: len(models) (each step includes consultation + response)
            request.total_steps = len(self.models_to_consult)
        elif not self.models_to_consult:
            raise ToolExecutionError("Consensus configuration is unavailable; restart with step_number=1 and models.")
        else:
            request.total_steps = len(self.models_to_consult)

        # For all steps (1 through total_steps), consult the corresponding model
        if request.step_number <= request.total_steps:
            # Calculate which model to consult for this step
            model_idx = request.step_number - 1  # 0-based index

            if model_idx < len(self.models_to_consult):
                # Track workflow state for conversation memory
                step_data = self.prepare_step_data(request)
                self.work_history.append(step_data)
                self._update_consolidated_findings(step_data)

                # Consult the model for this step
                model_response = await self._consult_model(self.models_to_consult[model_idx], request)

                # Add to accumulated responses
                self.accumulated_responses.append(model_response)

                # Include the model response in the step data
                response_data = {
                    "status": "model_consulted",
                    "step_number": request.step_number,
                    "total_steps": request.total_steps,
                    "model_consulted": model_response["model"],
                    "model_stance": model_response.get("stance", "neutral"),
                    "model_response": model_response,
                    "current_model_index": model_idx + 1,
                    "next_step_required": request.step_number < request.total_steps,
                }

                # Add CLAI Agent's analysis to step 1
                if request.step_number == 1:
                    response_data["agent_analysis"] = {
                        "initial_analysis": request.step,
                        "findings": request.findings,
                    }
                    response_data["status"] = "analysis_and_first_model_consulted"

                # Check if this is the final step
                if request.step_number == request.total_steps:
                    self._complete_consensus(response_data)
                else:
                    if model_response["status"] == "success":
                        response_data["next_steps"] = (
                            f"Model {model_response['model']} provided its {model_response.get('stance', 'neutral')} "
                            f"perspective. Analyze it and call {self.get_name()} again with "
                            f"step_number: {request.step_number + 1} and findings summarizing its key points."
                        )
                    else:
                        response_data["next_steps"] = (
                            f"Model {model_response['model']} failed: {model_response['error']}. "
                            f"Record the failure and call {self.get_name()} with step_number: "
                            f"{request.step_number + 1} to consult the next participant."
                        )

                # Add continuation information and workflow customization
                response_data = self.customize_workflow_response(response_data, request)

                # Ensure consensus-specific metadata is attached
                self._add_workflow_metadata(response_data, arguments)

                if continuation_id:
                    self.store_conversation_turn(continuation_id, response_data, request)
                    if not response_data.get("metadata", {}).get("memory_persistence_warning"):
                        try:
                            continuation_offer = self._build_continuation_offer(continuation_id)
                            if continuation_offer:
                                response_data["continuation_offer"] = continuation_offer
                        except ConversationMemoryError as exc:
                            response_data.setdefault("metadata", {}).update(memory_persistence_warning(exc))

                return [TextContent(type="text", text=json.dumps(response_data, indent=2, ensure_ascii=False))]

        # Otherwise, use standard workflow execution
        return await super().execute_workflow(arguments)

    def _build_continuation_offer(self, continuation_id: str) -> dict[str, Any] | None:
        """Create a continuation offer without exposing prior model responses."""
        return continuation_offer(continuation_id)

    async def _consult_model(self, model_config: dict, request) -> dict:
        """Consult a single model and return its response."""
        invocation = current_invocation(self)
        if invocation:
            invocation.provider_failed = True
        try:
            # Import and create ModelContext once at the beginning
            from utils.model_context import ModelContext

            # Get the provider for this model
            model_name = model_config["model"]
            provider = self.get_model_provider(model_name)

            # Create model context once and reuse for both file processing and temperature validation
            model_context = ModelContext(model_name=model_name)

            # Prepare the prompt with any relevant files
            # Use continuation_id=None for blinded consensus - each model should only see
            # original prompt + files, not conversation history or other model responses
            # CRITICAL: Use the original proposal from step 1, NOT what's in request.step for steps 2+!
            # Steps 2+ contain summaries/notes that must NEVER be sent to other models
            prompt = self.original_proposal if self.original_proposal else self.initial_prompt
            if request.relevant_files:
                file_content, _ = self._prepare_file_content_for_prompt(
                    request.relevant_files,
                    None,  # Use None instead of request.continuation_id for blinded consensus
                    "Context files",
                    model_context=model_context,
                )
                if file_content:
                    prompt = f"{prompt}\n\n=== CONTEXT FILES ===\n{file_content}\n=== END CONTEXT ==="

            # Get stance-specific system prompt
            stance = model_config.get("stance", "neutral")
            stance_prompt = model_config.get("stance_prompt")
            system_prompt = self._get_stance_enhanced_prompt(stance, stance_prompt)

            # Validate temperature against model constraints (respects supports_temperature)
            validated_temperature, temp_warnings = self.validate_and_correct_temperature(
                self.get_default_temperature(), model_context
            )

            # Log any temperature corrections
            for warning in temp_warnings:
                logger.warning(warning)

            # Call the model with validated temperature
            output_limit = get_request_budget(
                provider, model_name, prompt=prompt, system_prompt=system_prompt, images=request.images
            ).response_tokens
            self._record_provider_attempt(model_name, provider)
            response = await provider.generate_content(
                prompt=prompt,
                model_name=model_name,
                max_output_tokens=output_limit,
                system_prompt=system_prompt,
                temperature=validated_temperature,
                thinking_mode="medium",
                images=request.images if request.images else None,
            )
            if not response.content or not response.content.strip():
                raise ToolExecutionError("Model returned an empty response")
            if invocation:
                invocation.provider_failed = False
                invocation.reply_content = response.content or None

            return {
                "model": model_name,
                "stance": stance,
                "status": "success",
                "verdict": response.content,
                "metadata": {
                    "provider": provider.get_provider_type().value,
                    "model_name": model_name,
                },
            }

        except Exception as e:
            logger.exception("Error consulting model %s", model_config)
            return {
                "model": model_config.get("model", "unknown"),
                "stance": model_config.get("stance", "neutral"),
                "status": "error",
                "error": str(e),
            }

    def _get_stance_enhanced_prompt(self, stance: str, custom_stance_prompt: str | None = None) -> str:
        """Get the system prompt with stance injection."""
        base_prompt = CONSENSUS_PROMPT

        if custom_stance_prompt:
            return base_prompt.replace("{stance_prompt}", custom_stance_prompt)

        stance_prompts = {
            "for": """SUPPORTIVE PERSPECTIVE WITH INTEGRITY

You are tasked with advocating FOR this proposal, but with CRITICAL GUARDRAILS:

MANDATORY ETHICAL CONSTRAINTS:
- This is NOT a debate for entertainment. You MUST act in good faith and in the best interest of the questioner
- You MUST think deeply about whether supporting this idea is safe, sound, and passes essential requirements
- You MUST be direct and unequivocal in saying "this is a bad idea" when it truly is
- There must be at least ONE COMPELLING reason to be optimistic, otherwise DO NOT support it

WHEN TO REFUSE SUPPORT (MUST OVERRIDE STANCE):
- If the idea is fundamentally harmful to users, project, or stakeholders
- If implementation would violate security, privacy, or ethical standards
- If the proposal is technically infeasible within realistic constraints
- If costs/risks dramatically outweigh any potential benefits

YOUR SUPPORTIVE ANALYSIS SHOULD:
- Identify genuine strengths and opportunities
- Propose solutions to overcome legitimate challenges
- Highlight synergies with existing systems
- Suggest optimizations that enhance value
- Present realistic implementation pathways

Remember: Being "for" means finding the BEST possible version of the idea IF it has merit, not blindly supporting bad ideas.""",
            "against": """CRITICAL PERSPECTIVE WITH RESPONSIBILITY

You are tasked with critiquing this proposal, but with ESSENTIAL BOUNDARIES:

MANDATORY FAIRNESS CONSTRAINTS:
- You MUST NOT oppose genuinely excellent, common-sense ideas just to be contrarian
- You MUST acknowledge when a proposal is fundamentally sound and well-conceived
- You CANNOT give harmful advice or recommend against beneficial changes
- If the idea is outstanding, say so clearly while offering constructive refinements

WHEN TO MODERATE CRITICISM (MUST OVERRIDE STANCE):
- If the proposal addresses critical user needs effectively
- If it follows established best practices with good reason
- If benefits clearly and substantially outweigh risks
- If it's the obvious right solution to the problem

YOUR CRITICAL ANALYSIS SHOULD:
- Identify legitimate risks and failure modes
- Point out overlooked complexities
- Suggest more efficient alternatives
- Highlight potential negative consequences
- Question assumptions that may be flawed

Remember: Being "against" means rigorous scrutiny to ensure quality, not undermining good ideas that deserve support.""",
            "neutral": """BALANCED PERSPECTIVE

Provide objective analysis considering both positive and negative aspects. However, if there is overwhelming evidence
that the proposal clearly leans toward being exceptionally good or particularly problematic, you MUST accurately
reflect this reality. Being "balanced" means being truthful about the weight of evidence, not artificially creating
50/50 splits when the reality is 90/10.

Your analysis should:
- Present all significant pros and cons discovered
- Weight them according to actual impact and likelihood
- If evidence strongly favors one conclusion, clearly state this
- Provide proportional coverage based on the strength of arguments
- Help the questioner see the true balance of considerations

Remember: Artificial balance that misrepresents reality is not helpful. True balance means accurate representation
of the evidence, even when it strongly points in one direction.""",
        }

        stance_prompt = stance_prompts.get(stance, stance_prompts["neutral"])
        return base_prompt.replace("{stance_prompt}", stance_prompt)

    def customize_workflow_response(self, response_data: dict, request) -> dict:
        """Customize response for consensus workflow."""
        # Store model responses in the response for tracking
        if self.accumulated_responses:
            response_data["accumulated_responses"] = self.accumulated_responses

        # Add consensus-specific fields
        if "complete_consensus" in response_data:
            response_data["consensus_workflow_status"] = response_data["complete_consensus"]["status"]
        elif request.step_number == 1:
            response_data["consensus_workflow_status"] = "initial_analysis_complete"
        elif request.step_number < request.total_steps - 1:
            response_data["consensus_workflow_status"] = "consulting_models"
        else:
            response_data["consensus_workflow_status"] = "ready_for_synthesis"

        # Customize metadata for consensus workflow
        self._customize_consensus_metadata(response_data, request)

        return response_data

    def _customize_consensus_metadata(self, response_data: dict, request) -> None:
        """
        Customize metadata for consensus workflow to accurately reflect multi-model nature.

        The default workflow metadata shows the model running Agent's analysis steps,
        but consensus is a multi-model tool that consults different models. We need
        to provide accurate metadata that reflects this.
        """
        if "metadata" not in response_data:
            response_data["metadata"] = {}

        metadata = response_data["metadata"]

        # Always preserve tool_name
        metadata["tool_name"] = self.get_name()

        if "complete_consensus" in response_data:
            summary = response_data["complete_consensus"]
            metadata.update(
                {
                    "workflow_type": "multi_model_consensus",
                    "models_consulted": summary["models_consulted"],
                    "consensus_complete": response_data["consensus_complete"],
                    "total_models": summary["requested_models"],
                    "successful_models": summary["successful_models"],
                    "failed_models": summary["failed_models"],
                    "pending_models": summary["pending_models"],
                    "successful_providers": summary["successful_providers"],
                }
            )
            metadata.pop("model_used", None)
            metadata.pop("provider_used", None)

        else:
            # Intermediate steps - show consensus workflow in progress
            models_to_consult = []
            if self.models_to_consult:
                models_to_consult = [f"{m['model']}:{m.get('stance', 'neutral')}" for m in self.models_to_consult]

            metadata.update(
                {
                    "workflow_type": "multi_model_consensus",
                    "models_to_consult": models_to_consult,
                    "consultation_step": request.step_number,
                    "total_consultation_steps": request.total_steps,
                }
            )

            # Remove the misleading single model metadata that shows Agent's execution model
            # instead of the models being consulted
            metadata.pop("model_used", None)
            metadata.pop("provider_used", None)

    def _add_workflow_metadata(self, response_data: dict, arguments: dict[str, Any]) -> None:
        """
        Override workflow metadata addition for consensus tool.

        The consensus tool doesn't use single model metadata because it's a multi-model
        workflow. Instead, we provide consensus-specific metadata that accurately
        reflects the models being consulted.
        """
        # Initialize metadata if not present
        if "metadata" not in response_data:
            response_data["metadata"] = {}

        # Add basic tool metadata
        response_data["metadata"]["tool_name"] = self.get_name()

        # The consensus-specific metadata is already added by _customize_consensus_metadata
        # which is called from customize_workflow_response. We don't add the standard
        # single-model metadata (model_used, provider_used) because it's misleading
        # for a multi-model consensus workflow.

        logger.debug(
            f"[CONSENSUS_METADATA] {self.get_name()}: Using consensus-specific metadata instead of single-model metadata"
        )

    def store_initial_issue(self, step_description: str):
        """Store initial prompt for model consultations."""
        self.original_proposal = step_description
        self.initial_prompt = step_description  # Keep for backward compatibility

    # Required abstract methods from BaseTool
    def get_request_model(self):
        """Return the consensus workflow-specific request model."""
        return ConsensusRequest

    async def prepare_prompt(self, request) -> str:  # noqa: ARG002
        """Not used - workflow tools use execute_workflow()."""
        return ""  # Workflow tools use execute_workflow() directly
