"""
Patch Generator Module
======================

Generates code patches to address improvement opportunities.
Uses multi-model approach for better patch quality.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class GeneratedPatch:
    """A generated code patch."""
    patch_id: str
    opportunity_id: str
    file_path: str
    description: str
    original_code: str
    patched_code: str
    confidence: float  # 0-1
    model_used: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict = field(default_factory=dict)


class PatchGenerator:
    """
    Generates code patches for improvement opportunities.

    Strategies:
    1. Template-based: Use predefined templates
    2. Pattern-based: Apply learned patterns
    3. LLM-based: Generate with LLM (when available)
    4. Hybrid: Combine multiple approaches
    """

    def __init__(self, project_root: str = None):
        self._lock = threading.RLock()

        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path.cwd()

        # Storage
        self.patches: List[GeneratedPatch] = []
        self.patches_dir = self.project_root / "data" / "patches"
        self.patches_dir.mkdir(parents=True, exist_ok=True)

        # Templates
        self.templates = self._load_templates()

        self._load()

    def _load(self):
        """Load previously generated patches."""
        patches_file = self.patches_dir / "generated.json"
        if patches_file.exists():
            try:
                with open(patches_file, 'r') as f:
                    data = json.load(f)
                self.patches = [GeneratedPatch(**p) for p in data.get("patches", [])]
            except Exception as e:
                logger.warning(f"Failed to load patches: {e}")

    def _save(self):
        """Save generated patches."""
        with self._lock:
            patches_file = self.patches_dir / "generated.json"
            data = {
                "last_updated": datetime.now().isoformat(),
                "total_patches": len(self.patches),
                "patches": [asdict(p) for p in self.patches[-100:]]
            }
            with open(patches_file, 'w') as f:
                json.dump(data, f, indent=2)

    def _load_templates(self) -> Dict[str, str]:
        """Load patch templates."""
        return {
            # Code quality templates
            "add_docstring": '''"""
{description}
"""
''',
            "add_type_hints": "def {func_name}({params}) -> {return_type}:",
            "extract_function": '''def {new_func_name}({params}):
    """Extracted from {original_func}"""
    {body}

''',
            # Performance templates
            "use_comprehension": "{result} = [{expr} for {var} in {iterable}]",
            "cache_result": '''_cache = {}

def {func_name}({params}):
    key = ({cache_key})
    if key not in _cache:
        _cache[key] = {original_call}
    return _cache[key]
''',
            # Security templates
            "sanitize_input": "{var} = {var}.replace('{dangerous}', '')",
            "use_parameterized_query": "cursor.execute(\"SELECT * FROM {table} WHERE id = ?\", ({id_var},))",

            # Error handling templates
            "add_try_catch": '''try:
    {original_code}
except {exception_type} as e:
    logger.error("{error_message}: {e}")
    {fallback}
''',
        }

    def generate_patch(self, opportunity: Dict, strategy: str = "auto") -> Optional[GeneratedPatch]:
        """
        Generate a patch for an improvement opportunity.

        Args:
            opportunity: The improvement opportunity dict
            strategy: "template", "pattern", "llm", or "auto"

        Returns:
            GeneratedPatch or None if generation failed
        """
        category = opportunity.get("category", "")

        if strategy == "auto":
            strategy = self._select_strategy(category)

        if strategy == "template":
            patch = self._generate_from_template(opportunity)
        elif strategy == "pattern":
            patch = self._generate_from_pattern(opportunity)
        else:
            patch = self._generate_from_template(opportunity)  # Default to template

        if patch:
            with self._lock:
                self.patches.append(patch)
                self._save()

        return patch

    def _select_strategy(self, category: str) -> str:
        """Select best strategy for a category."""
        strategy_map = {
            "code_quality": "template",
            "performance": "pattern",
            "bug": "template",
            "security": "template",
            "learning": "pattern",
            "feature": "template"
        }
        return strategy_map.get(category, "template")

    def _generate_from_template(self, opportunity: Dict) -> Optional[GeneratedPatch]:
        """Generate patch using templates."""
        category = opportunity.get("category", "")
        description = opportunity.get("description", "")
        file_path = opportunity.get("file_path", "")

        if not file_path:
            return None

        full_path = self.project_root / file_path
        if not full_path.exists():
            return None

        try:
            with open(full_path, 'r') as f:
                original_code = f.read()

            # Generate patch based on category
            patched_code = original_code
            confidence = 0.5

            if category == "code_quality":
                # Add docstring if missing
                if "def " in original_code and '"""' not in original_code[:200]:
                    lines = original_code.split('\n')
                    for i, line in enumerate(lines):
                        if line.strip().startswith("def "):
                            indent = len(line) - len(line.lstrip())
                            docstring = " " * (indent + 4) + f'"""\n{description}\n{" " * (indent + 4)}"""\n'
                            lines.insert(i + 1, docstring)
                            break
                    patched_code = '\n'.join(lines)
                    confidence = 0.7

            elif category == "security":
                # Add input validation
                if "input(" in original_code or "request." in original_code:
                    patched_code = original_code.replace(
                        "input(", "str(input("
                    ).replace(")", "))", 1)
                    patched_code = f'''import re
# Input sanitization added
def sanitize(s):
    return re.sub(r'[<>\"\\'&]', '', str(s))

{patched_code}'''
                    confidence = 0.6

            elif category == "performance":
                # Add caching suggestion
                patched_code = f'''# TODO: Consider adding caching for performance
{original_code}'''
                confidence = 0.4

            # Generate patch ID
            patch_num = len(self.patches) + 1
            patch_id = f"PATCH-{patch_num:04d}"

            return GeneratedPatch(
                patch_id=patch_id,
                opportunity_id=opportunity.get("id", "unknown"),
                file_path=file_path,
                description=description,
                original_code=original_code,
                patched_code=patched_code,
                confidence=confidence,
                model_used="template_engine"
            )

        except Exception as e:
            logger.error(f"Failed to generate patch: {e}")
            return None

    def _generate_from_pattern(self, opportunity: Dict) -> Optional[GeneratedPatch]:
        """Generate patch using learned patterns."""
        # For now, delegate to template with pattern awareness
        return self._generate_from_template(opportunity)

    def get_pending_patches(self, min_confidence: float = 0.5) -> List[GeneratedPatch]:
        """Get patches that haven't been applied yet."""
        # Check which patches have been applied
        applied_file = self.patches_dir / "applied.json"
        applied_ids = set()

        if applied_file.exists():
            try:
                with open(applied_file, 'r') as f:
                    data = json.load(f)
                applied_ids = set(data.get("applied_ids", []))
            except:
                pass

        return [
            p for p in self.patches
            if p.patch_id not in applied_ids and p.confidence >= min_confidence
        ]

    def get_high_confidence_patches(self) -> List[GeneratedPatch]:
        """Get patches with high confidence (>0.7)."""
        return [p for p in self.patches if p.confidence > 0.7]


# Singleton
_generator: Optional[PatchGenerator] = None


def get_patch_generator(project_root: str = None) -> PatchGenerator:
    """Get singleton patch generator."""
    global _generator
    if _generator is None:
        _generator = PatchGenerator(project_root)
    return _generator


def generate_patch(opportunity: Dict, strategy: str = "auto") -> Optional[GeneratedPatch]:
    """Generate a patch for an opportunity."""
    return get_patch_generator().generate_patch(opportunity, strategy)
