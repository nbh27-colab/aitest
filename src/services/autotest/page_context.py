#----------------------------------------------#
# Extract Page Context from Playwright Page    #
#----------------------------------------------#

import base64
from typing import Any, Dict, Optional, List
from datetime import datetime
from playwright.sync_api import Page

def extract_dom_snapshot(page: Page) -> Dict[str, Any]:
    """
    Extract full DOM snapshot with HTML structure, accessibility tree, layout info
    """
    try:
        snapshot = page.evaluate("""
            () => {
                // 1. Get simplified HTML structure (interactive elements only)
                function getInteractiveHTML(element, depth = 0) {
                    if (depth > 5) return null; // Limit depth to avoid too large output
                    
                    const interactiveTags = ['button', 'a', 'input', 'select', 'textarea', 'form', 'label'];
                    const isInteractive = interactiveTags.includes(element.tagName.toLowerCase()) ||
                                         element.hasAttribute('onclick') ||
                                         element.hasAttribute('role') ||
                                         element.classList.contains('clickable') ||
                                         // AUI/Angular UI custom components
                                         element.tagName.toLowerCase().startsWith('aui-') ||
                                         element.classList.contains('aui-comboboxshell') ||
                                         element.hasAttribute('data-trigger');
                    
                    const rect = element.getBoundingClientRect();
                    const isVisible = rect.width > 0 && rect.height > 0 && 
                                     window.getComputedStyle(element).display !== 'none';
                    
                    // SPECIAL: Include labels even if not directly interactive, to show label->input relationships
                    const isFormLabel = element.tagName.toLowerCase() === 'label';
                    
                    if (!isVisible && !isInteractive && !isFormLabel) return null;
                    
                    let result = {
                        tag: element.tagName.toLowerCase(),
                        text: (element.textContent || '').trim().substring(0, 50),
                        attrs: {},
                        children: []
                    };
                    
                    // Collect important attributes
                    ['id', 'class', 'name', 'type', 'placeholder', 'href', 'role', 'aria-label', 'data-testid', 'title', 'for', 'data-trigger', 'data-value', 'u:id'].forEach(attr => {
                        if (element.hasAttribute(attr)) {
                            result.attrs[attr] = element.getAttribute(attr);
                        }
                    });
                    
                    // Add value for inputs
                    if (element.value !== undefined) {
                        result.attrs.value = element.value;
                    }
                    
                    // SPECIAL: For select elements, capture available options
                    if (element.tagName.toLowerCase() === 'select') {
                        const options = Array.from(element.options || []).map(opt => ({
                            text: opt.text,
                            value: opt.value,
                            selected: opt.selected
                        }));
                        if (options.length > 0) {
                            result.attrs.options = options;
                        }
                        // Check if it's a custom dropdown (has specific classes)
                        if (element.classList.contains('action') || 
                            element.classList.contains('p-dropdown') ||
                            element.getAttribute('aria-haspopup') === 'listbox') {
                            result.attrs.customDropdown = true;
                        }
                    }
                    
                    // Recursively process children (only if interactive or container)
                    // CRITICAL: For form containers (div, section), always include children to capture label+input pairs
                    const isFormContainer = ['div', 'section', 'main', 'nav', 'form', 'ul', 'ol', 'table', 'tr', 'tbody', 'fieldset'].includes(result.tag);
                    
                    if (isInteractive || isFormContainer) {
                        for (let child of element.children) {
                            const childResult = getInteractiveHTML(child, depth + 1);
                            if (childResult) {
                                result.children.push(childResult);
                            }
                        }
                    }
                    
                    return result;
                }
                
                // 2. Get accessibility tree
                function getAccessibilityInfo() {
                    const accessible = [];
                    const selectors = '[role], button, a, input, select, textarea';
                    document.querySelectorAll(selectors).forEach((el, idx) => {
                        if (el.offsetParent !== null) { // visible check
                            accessible.push({
                                index: idx,
                                role: el.getAttribute('role') || el.tagName.toLowerCase(),
                                label: el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.textContent?.trim().substring(0, 30),
                                name: el.getAttribute('name'),
                                id: el.id
                            });
                        }
                    });
                    return accessible.slice(0, 30);
                }
                
                return {
                    html_tree: getInteractiveHTML(document.body),
                    accessibility_tree: getAccessibilityInfo(),
                    page_structure: {
                        has_modals: document.querySelectorAll('[role="dialog"], .modal').length > 0,
                        has_overlays: document.querySelectorAll('.overlay, .backdrop').length > 0,
                        has_forms: document.querySelectorAll('form').length,
                        has_tables: document.querySelectorAll('table').length
                    }
                };
            }
        """)

        return snapshot
    except Exception as e:
        print(f"Error extracting DOM snapshot: {e}")
        return {
            "html_tree": None,
            "accessibility_tree": [],
            "page_structure": {},
            "error": str(e)
        }
    
def _get_page_context_sync(page: Page, previous_results: List[Dict] = None) -> Dict[str, Any]:
    """
    Sync version - collects page context using sync Playwright API
    """
    try:
        # Current URL
        current_url = page.url

        # page title
        page_title = page.title()

        # main heading
        main_heading = None
        if page.locator('h1').count() > 0:
            try:
                main_heading = page.locator('h1').first.text_content()
            except:
                pass
        
        # visible interactive elements
        visible_elements = page.evaluate("""
            () => {
                const elements = [];
                const selectors = 'button, a, input, select, textarea, [role="button"], [onclick], [data-trigger], aui-comboboxshell';
                document.querySelectorAll(selectors).forEach((el, index) => {
                    // Check if element is visible
                    if (el.offsetParent !== null && el.offsetWidth > 0 && el.offsetHeight > 0) {
                        const rect = el.getBoundingClientRect();
                        const styles = window.getComputedStyle(el);
                        
                        elements.push({
                            index: index,
                            tag: el.tagName.toLowerCase(),
                            text: (el.innerText || el.textContent || el.value || '').trim().substring(0, 100),
                            id: el.id || null,
                            class: el.className || null,
                            name: el.name || null,
                            type: el.type || null,
                            placeholder: el.placeholder || null,
                            href: el.href || null,
                            role: el.getAttribute('role') || null,
                            ariaLabel: el.getAttribute('aria-label') || null,
                            // For select elements, capture options and detect custom dropdowns
                            isCustomDropdown: el.tagName.toLowerCase() === 'select' && (
                                el.classList.contains('action') ||
                                el.classList.contains('p-dropdown') ||
                                el.getAttribute('aria-haspopup') === 'listbox'
                            ),
                            options: el.tagName.toLowerCase() === 'select' ? 
                                Array.from(el.options || []).map(opt => opt.text).slice(0, 10) : null,
                            // AUI framework detection
                            isAUIComponent: el.tagName.toLowerCase().startsWith('aui-') || 
                                           el.classList.contains('aui-comboboxshell') ||
                                           el.hasAttribute('data-trigger'),
                            auiTrigger: el.getAttribute('data-trigger') || null,
                            position: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height)
                            },
                            visible: styles.display !== 'none' && styles.visibility !== 'hidden'
                        });
                    }
                });
                return elements.slice(0, 50); // Limit to 50 elements
            }
        """)

        # screenshot
        screenshot_bytes = page.screenshot(type='png', full_page=True)
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

        # console logs
        console_logs = []

        # previous results summary
        previous_summary = []

        if previous_results:
            for i, result in enumerate(previous_results[-5:]):
                previous_summary.append({
                    "substep": len(previous_results) - 5 + i + 1 if len(previous_results) > 5 else i + 1,
                    "success": result.get("success", False),
                    "message": result.get("message", ""),
                    "error": result.get("error", None)
                })

        # full DOM snapshot
        dom_snapshot = extract_dom_snapshot(page)

        context = {
            "current_url": current_url,
            "page_title": page_title,
            "main_heading": main_heading,
            "visible_elements": visible_elements,
            "dom_snapshot": dom_snapshot,
            "screenshot_base64": screenshot_base64,
            "console_logs": console_logs,
            "previous_results": previous_summary,
            "timestamp": datetime.now().isoformat()
        }

        return context
        
    except Exception as e:
        print(f"Error getting page context: {e}")
        return {
            "current_url": page.url if page else "unknown",
            "page_title": "Error",
            "main_heading": None,
            "visible_elements": [],
            "dom_snapshot": {"html_tree": None, "accessibility_tree": [], "page_structure": {}},
            "screenshot_base64": "",
            "console_logs": [],
            "previous_results": [],
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }
    

def format_html_tree(tree: Dict, indent: int = 0) -> str:
    """Format HTML tree structure for LLM readability with clear form element relationships"""
    if not tree:
        return ""
    
    lines = []
    prefix = "  " * indent
    
    # Format opening tag with attributes
    tag = tree['tag']
    attrs = tree.get('attrs', {})
    
    # CRITICAL: Highlight important form attributes
    important_attrs = []
    if 'id' in attrs:
        important_attrs.append(f'id="{attrs["id"]}"')
    if 'name' in attrs:
        important_attrs.append(f'name="{attrs["name"]}"')
    if 'type' in attrs:
        important_attrs.append(f'type="{attrs["type"]}"')
    if 'role' in attrs:
        important_attrs.append(f'role="{attrs["role"]}"')
    if 'for' in attrs:
        important_attrs.append(f'for="{attrs["for"]}"')
    if 'class' in attrs and attrs['class']:
        # Only show first few classes to reduce noise
        class_list = str(attrs['class']).split()[:3]
        important_attrs.append(f'class="{" ".join(class_list)}"')
    
    attr_str = " ".join(important_attrs)
    
    # Special formatting for form elements
    if tag in ['input', 'select', 'textarea', 'button']:
        visual_marker = "🔹"  # Make form controls stand out
        
        # For selects, add options info and custom dropdown indicator
        if tag == 'select':
            options_info = ""
            if attrs.get('options'):
                option_texts = [opt.get('text', '') for opt in attrs['options'][:5]]  # Show first 5 options
                options_info = f" [Options: {', '.join(option_texts)}...]"
            
            custom_indicator = ""
            if attrs.get('customDropdown'):
                custom_indicator = " ⚠️CUSTOM_DROPDOWN"
            
            if attr_str:
                lines.append(f"{prefix}{visual_marker}<{tag} {attr_str}>{custom_indicator}{options_info}")
            else:
                lines.append(f"{prefix}{visual_marker}<{tag}>{custom_indicator}{options_info}")
        else:
            if attr_str:
                lines.append(f"{prefix}{visual_marker}<{tag} {attr_str}>")
            else:
                lines.append(f"{prefix}{visual_marker}<{tag}>")
    elif tag == 'label':
        visual_marker = "📋"  # Make labels stand out
        label_text = tree.get('text', '')[:50]
        if attr_str:
            lines.append(f"{prefix}{visual_marker}<{tag} {attr_str}> {label_text}")
        else:
            lines.append(f"{prefix}{visual_marker}<{tag}> {label_text}")
    else:
        if attr_str:
            lines.append(f"{prefix}<{tag} {attr_str}>")
        else:
            lines.append(f"{prefix}<{tag}>")
    
    # Add text content if exists and no children (skip for labels, already shown)
    if tree.get('text') and not tree.get('children') and tag != 'label':
        lines.append(f"{prefix}  {tree['text']}")
    
    # Recursively format children
    for child in tree.get('children', []):
        lines.append(format_html_tree(child, indent + 1))
    
    lines.append(f"{prefix}</{tag}>")
    
    return "\n".join(lines)

def format_elements_for_llm(elements: List[Dict]) -> str:
    """Format visible elements thành text dễ đọc cho LLM"""
    if not elements:
        return "No interactive elements found"
    
    formatted = []
    for el in elements[:30]:  # Limit to 30 for token efficiency
        parts = [f"[{el['tag'].upper()}]"]
        
        if el.get('text'):
            parts.append(f"'{el['text'][:50]}'")
        
        identifiers = []
        if el.get('id'):
            identifiers.append(f"id={el['id']}")
        if el.get('name'):
            identifiers.append(f"name={el['name']}")
        if el.get('type'):
            identifiers.append(f"type={el['type']}")
        if el.get('role'):
            identifiers.append(f"role={el['role']}")
        
        if identifiers:
            parts.append(f"({', '.join(identifiers)})")
        
        if el.get('placeholder'):
            parts.append(f"placeholder='{el['placeholder']}'")
        
        # CRITICAL: Show custom dropdown info
        if el.get('isCustomDropdown'):
            parts.append("⚠️CUSTOM_DROPDOWN")
            if el.get('options'):
                options_preview = ', '.join(el['options'][:5])
                parts.append(f"[Options: {options_preview}]")
        elif el.get('isAUIComponent'):
            parts.append("⚠️AUI_COMPONENT")
            if el.get('auiTrigger'):
                parts.append(f"[trigger={el['auiTrigger']}]")
        elif el.get('options'):
            options_preview = ', '.join(el['options'][:5])
            parts.append(f"[Options: {options_preview}]")
        
        formatted.append(" ".join(parts))
    
    return "\n".join(formatted)

def format_context_for_llm(context: Dict[str, Any]) -> str:
    """
    Format toàn bộ context thành prompt text cho LLM
    includes DOM structure and previous errors
    """
    parts = [
        f"Current URL: {context['current_url']}",
        f"Page Title: {context['page_title']}",
    ]
    
    if context.get('main_heading'):
        parts.append(f"Main Heading: {context['main_heading']}")
    
    # Add DOM snapshot if available
    dom_snapshot = context.get('dom_snapshot', {})
    if dom_snapshot and dom_snapshot.get('html_tree'):
        parts.append("\n=== Page HTML Structure ===")
        parts.append(format_html_tree(dom_snapshot['html_tree']))
        
        # Add page structure info
        structure = dom_snapshot.get('page_structure', {})
        if structure:
            parts.append("\n=== Page Structure Info ===")
            if structure.get('has_modals'):
                parts.append(f"[!] Active modals/dialogs detected: {structure['has_modals']}")
            if structure.get('has_overlays'):
                parts.append(f"[!] Overlays detected: {structure['has_overlays']}")
            if structure.get('has_forms'):
                parts.append(f"[!] Forms on page: {structure['has_forms']}")
            if structure.get('has_tables'):
                parts.append(f"[!] Tables on page: {structure['has_tables']}")

    # Legacy visible elements (fallback)
    parts.append("\n=== Visible Interactive Elements ===")
    parts.append(format_elements_for_llm(context.get('visible_elements', [])))
    
    # Previous results with ERROR DETAILS
    if context.get('previous_results'):
        parts.append("\n=== Previous SubSteps History ===")
        for result in context['previous_results']:
            status = "[SUCCESS]" if result['success'] else "[FAILED]"
            parts.append(f"  {status} - SubStep {result['substep']}: {result['message']}")
            
            # IMPORTANT: Show error details to help LLM learn
            if not result['success'] and result.get('error'):
                error_msg = result['error']
                # Extract useful info from error
                if 'Timeout' in error_msg:
                    parts.append(f"    [!] Error: Element not found (timeout)")
                elif 'SyntaxError' in error_msg or 'not a valid selector' in error_msg:
                    parts.append(f"    [!] Error: Invalid CSS selector syntax")
                elif 'not visible' in error_msg:
                    parts.append(f"    [!]  Error: Element exists but not visible")
                else:
                    parts.append(f"    [!] Error: {error_msg[:200]}")

    return "\n".join(parts)


# Async wrapper for calling from async context
import asyncio
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=1)

async def get_page_context(page, previous_results: List[Dict] = None) -> Dict[str, Any]:
    """
    Async wrapper for get_page_context - runs sync version in executor
    Accepts both AsyncPageWrapper and sync Page
    """
    # Unwrap if it's AsyncPageWrapper
    from src.services.autotest.nodes import AsyncPageWrapper
    if isinstance(page, AsyncPageWrapper):
        sync_page = page._page
        executor = page._executor
    else:
        sync_page = page
        executor = _executor
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, _get_page_context_sync, sync_page, previous_results)