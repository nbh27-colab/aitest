"""
Extract page context từ Playwright page
"""

import base64
from typing import Dict, Any, List, Optional
from datetime import datetime
from playwright.async_api import Page


async def extract_dom_snapshot(page: Page) -> Dict[str, Any]:
    """
    Extract full DOM snapshot with HTML structure, accessibility tree, and layout info.
    This gives LLM complete picture of page state after each substep.
    """
    try:
        snapshot = await page.evaluate("""
            () => {
                // 1. Get simplified HTML structure (interactive elements only)
                function getInteractiveHTML(element, depth = 0) {
                    if (depth > 5) return null; // Limit depth to avoid too large output
                    
                    const interactiveTags = ['button', 'a', 'input', 'select', 'textarea', 'form'];
                    const isInteractive = interactiveTags.includes(element.tagName.toLowerCase()) ||
                                         element.hasAttribute('onclick') ||
                                         element.hasAttribute('role') ||
                                         element.classList.contains('clickable');
                    
                    const rect = element.getBoundingClientRect();
                    const isVisible = rect.width > 0 && rect.height > 0 && 
                                     window.getComputedStyle(element).display !== 'none';
                    
                    if (!isVisible && !isInteractive) return null;
                    
                    let result = {
                        tag: element.tagName.toLowerCase(),
                        text: (element.textContent || '').trim().substring(0, 50),
                        attrs: {},
                        children: []
                    };
                    
                    // Collect important attributes
                    ['id', 'class', 'name', 'type', 'placeholder', 'href', 'role', 'aria-label', 'data-testid', 'title'].forEach(attr => {
                        if (element.hasAttribute(attr)) {
                            result.attrs[attr] = element.getAttribute(attr);
                        }
                    });
                    
                    // Add value for inputs
                    if (element.value !== undefined) {
                        result.attrs.value = element.value;
                    }
                    
                    // Recursively process children (only if interactive or container)
                    if (isInteractive || ['div', 'section', 'main', 'nav', 'form', 'ul', 'ol', 'table', 'tr', 'tbody'].includes(result.tag)) {
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


async def get_page_context(page: Page, previous_results: List[Dict] = None) -> Dict[str, Any]:
    """
    Thu thập thông tin về trạng thái hiện tại của page
    để LLM có đủ context để generate substep tiếp theo
    
    UPDATED: Now includes full DOM snapshot for better LLM understanding
    """
    try:
        # 1. Current URL
        current_url = page.url
        
        # 2. Page title
        page_title = await page.title()
        
        # 3. Main heading
        main_heading = None
        if await page.locator('h1').count() > 0:
            try:
                main_heading = await page.locator('h1').first.text_content()
            except:
                pass
        
        # 4. Visible interactive elements (LEGACY - kept for backwards compatibility)
        visible_elements = await page.evaluate("""
            () => {
                const elements = [];
                const selectors = 'button, a, input, select, textarea, [role="button"], [onclick]';
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
        
        # 5. Screenshot (base64 encoded)
        screenshot_bytes = await page.screenshot(type='png', full_page=False)
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
        
        # 6. Console logs (recent)
        # Note: This would need to be tracked separately in the workflow
        console_logs = []
        
        # 7. Previous results summary (ENHANCED - includes errors)
        previous_summary = []
        if previous_results:
            for i, result in enumerate(previous_results[-5:]):  # Last 5 results
                previous_summary.append({
                    "substep": len(previous_results) - 5 + i + 1 if len(previous_results) > 5 else i + 1,
                    "success": result.get("success", False),
                    "message": result.get("message", ""),
                    "error": result.get("error", None)  # Include error details!
                })
        
        # 8. NEW: Extract full DOM snapshot for better context
        dom_snapshot = await extract_dom_snapshot(page)
        
        context = {
            "current_url": current_url,
            "page_title": page_title,
            "main_heading": main_heading,
            "visible_elements": visible_elements,  # Keep for backwards compatibility
            "dom_snapshot": dom_snapshot,  # NEW: Full HTML structure
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
    """Format HTML tree structure for LLM readability"""
    if not tree:
        return ""
    
    lines = []
    prefix = "  " * indent
    
    # Format opening tag with attributes
    tag = tree['tag']
    attrs = tree.get('attrs', {})
    attr_str = " ".join([f'{k}="{v}"' for k, v in attrs.items() if v])
    
    if attr_str:
        lines.append(f"{prefix}<{tag} {attr_str}>")
    else:
        lines.append(f"{prefix}<{tag}>")
    
    # Add text content if exists and no children
    if tree.get('text') and not tree.get('children'):
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
        
        formatted.append(" ".join(parts))
    
    return "\n".join(formatted)


def format_context_for_llm(context: Dict[str, Any]) -> str:
    """
    Format toàn bộ context thành prompt text cho LLM
    UPDATED: Now includes DOM structure and previous errors
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
                parts.append(f"⚠️ Active modals/dialogs detected: {structure['has_modals']}")
            if structure.get('has_overlays'):
                parts.append(f"⚠️ Overlays detected: {structure['has_overlays']}")
            if structure.get('has_forms'):
                parts.append(f"Forms on page: {structure['has_forms']}")
            if structure.get('has_tables'):
                parts.append(f"Tables on page: {structure['has_tables']}")
    
    # Legacy visible elements (fallback)
    parts.append("\n=== Visible Interactive Elements ===")
    parts.append(format_elements_for_llm(context.get('visible_elements', [])))
    
    # Previous results with ERROR DETAILS
    if context.get('previous_results'):
        parts.append("\n=== Previous SubSteps History ===")
        for result in context['previous_results']:
            status = "✓ SUCCESS" if result['success'] else "✗ FAILED"
            parts.append(f"  {status} - SubStep {result['substep']}: {result['message']}")
            
            # IMPORTANT: Show error details to help LLM learn
            if not result['success'] and result.get('error'):
                error_msg = result['error']
                # Extract useful info from error
                if 'Timeout' in error_msg:
                    parts.append(f"    ⚠️ Error: Element not found (timeout)")
                elif 'SyntaxError' in error_msg or 'not a valid selector' in error_msg:
                    parts.append(f"    ⚠️ Error: Invalid CSS selector syntax")
                elif 'not visible' in error_msg:
                    parts.append(f"    ⚠️ Error: Element exists but not visible")
                else:
                    parts.append(f"    ⚠️ Error: {error_msg[:200]}")
    
    return "\n".join(parts)
