"""Canonical hardware names in catalog metadata; native evidence stays unchanged."""
from copy import deepcopy
import re


NAME_FIELDS = frozenset({'hardware', 'gpu_hardware', 'title', 'label'})
SOURCE_FIELDS = frozenset({'source', 'sources', 'archive', 'evidence', 'provenance'})
H200_ALIAS = re.compile(r'\bH20Z\b', re.IGNORECASE)
B200_ALIAS = re.compile(r'(?<![a-z0-9_])([0-9]+x)?B20Z(?![a-z0-9_])', re.IGNORECASE | re.ASCII)


def normalize_hardware_names(value):
    """Return catalog metadata with H20Z names changed to H200.

    Preserve IDs, source locators, numbers, numerical settings and native device-name
    observations. Card counts and topology are retained, not erased by a rename.
    Apply to derived catalogs only, never to frozen source payloads or receipts.
    """
    if isinstance(value, list):
        return [normalize_hardware_names(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in SOURCE_FIELDS:
                result[key] = deepcopy(item)
            elif key in NAME_FIELDS and isinstance(item, str):
                result[key] = H200_ALIAS.sub('H200', item)
            else:
                result[key] = normalize_hardware_names(item)
        return result
    return value


def _canonical_b200_text(text):
    return B200_ALIAS.sub(r'\g<1>B200', text) if isinstance(text, str) else text


def canonical_b200_evaluation(record):
    """Copy one evaluation, correcting B20Z only in its public display fields."""
    def rename(container, field):
        if field in container:
            container[field] = _canonical_b200_text(container[field])

    result = deepcopy(record)
    rename(result, 'title')
    content = result.get('content')
    if isinstance(content, dict) and isinstance(content.get('title'), dict):
        for locale in content['title']:
            rename(content['title'], locale)
    if isinstance(result.get('differences'), list):
        result['differences'] = [_canonical_b200_text(text) for text in result['differences']]
    if isinstance(result.get('sides'), list):
        for side in result['sides']:
            if isinstance(side, dict):
                rename(side, 'hardware')
                rename(side, 'label')
    return result


def canonical_b200_case(row):
    """Copy one case entity, correcting B20Z only in its requested hardware."""
    result = deepcopy(row)
    details = result.get('details')
    if result.get('kind') == 'case' and isinstance(details, dict):
        request = details.get('request')
        if isinstance(request, dict) and 'hardware' in request:
            request['hardware'] = _canonical_b200_text(request['hardware'])
    return result
