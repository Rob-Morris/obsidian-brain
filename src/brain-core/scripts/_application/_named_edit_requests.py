"""Sealed collection of granular named-document mutation request types."""

from .memory.append import MemoryAppendRequest
from .memory.delete_section import MemoryDeleteSectionRequest
from .memory.edit import MemoryEditRequest
from .memory.prepend import MemoryPrependRequest
from .memory.replace_text import MemoryReplaceTextRequest
from .skill.append import SkillAppendRequest
from .skill.delete_section import SkillDeleteSectionRequest
from .skill.edit import SkillEditRequest
from .skill.prepend import SkillPrependRequest
from .skill.replace_text import SkillReplaceTextRequest
from .style.append import StyleAppendRequest
from .style.delete_section import StyleDeleteSectionRequest
from .style.edit import StyleEditRequest
from .style.prepend import StylePrependRequest
from .style.replace_text import StyleReplaceTextRequest
from .template.append import TemplateAppendRequest
from .template.delete_section import TemplateDeleteSectionRequest
from .template.edit import TemplateEditRequest
from .template.prepend import TemplatePrependRequest
from .template.replace_text import TemplateReplaceTextRequest


NamedEditRequest = (
    MemoryAppendRequest
    | MemoryDeleteSectionRequest
    | MemoryEditRequest
    | MemoryPrependRequest
    | MemoryReplaceTextRequest
    | SkillAppendRequest
    | SkillDeleteSectionRequest
    | SkillEditRequest
    | SkillPrependRequest
    | SkillReplaceTextRequest
    | StyleAppendRequest
    | StyleDeleteSectionRequest
    | StyleEditRequest
    | StylePrependRequest
    | StyleReplaceTextRequest
    | TemplateAppendRequest
    | TemplateDeleteSectionRequest
    | TemplateEditRequest
    | TemplatePrependRequest
    | TemplateReplaceTextRequest
)


NAMED_EDIT_REQUEST_TYPES = (
    MemoryAppendRequest,
    MemoryDeleteSectionRequest,
    MemoryEditRequest,
    MemoryPrependRequest,
    MemoryReplaceTextRequest,
    SkillAppendRequest,
    SkillDeleteSectionRequest,
    SkillEditRequest,
    SkillPrependRequest,
    SkillReplaceTextRequest,
    StyleAppendRequest,
    StyleDeleteSectionRequest,
    StyleEditRequest,
    StylePrependRequest,
    StyleReplaceTextRequest,
    TemplateAppendRequest,
    TemplateDeleteSectionRequest,
    TemplateEditRequest,
    TemplatePrependRequest,
    TemplateReplaceTextRequest,
)
