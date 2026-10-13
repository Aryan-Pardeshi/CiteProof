import os

import matplotlib

# Use Agg backend for headless container environment
matplotlib.use('Agg')
import uuid

import matplotlib.pyplot as plt
from langchain_core.tools import tool


@tool
def generate_matplotlib_chart(python_code: str) -> str:
    """Execute Python code containing matplotlib instructions to generate and save a chart.
    The code should plot data using standard matplotlib.pyplot (plt) functions.
    Do NOT call plt.show() — the execution wrapper will automatically save the figure to disk.
    Always define clean labels, title, and grid/legend where appropriate.
    Returns:
        The markdown image link referencing the generated chart image (e.g. ![Chart Description](http://localhost:8000/static/graphs/<uuid>.png))
        which you MUST insert into the appropriate section in the final markdown report.
    """
    # Clear any previous figures
    plt.clf()
    plt.close('all')
    
    # Execute the code in a dictionary namespace
    local_vars = {}
    try:
        # Standard matplotlib and pyplot are available inside exec
        exec_globals = {
            "plt": plt,
            "matplotlib": matplotlib,
            "__builtins__": __builtins__
        }
        exec(python_code, exec_globals, local_vars)  # noqa: S102 - executes generated chart code.
        
        # Save the figure
        static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "graphs"))
        os.makedirs(static_dir, exist_ok=True)
        
        filename = f"{uuid.uuid4()}.png"
        filepath = os.path.join(static_dir, filename)
        
        plt.savefig(filepath, bbox_inches='tight', dpi=150)
        plt.close('all')
        
        # Return the Markdown image syntax
        url = f"http://localhost:8000/static/graphs/{filename}"
        return f"![Generated Chart]({url})"
    except Exception as e:  # noqa: BLE001
        return f"Error executing matplotlib code: {e!s}"
