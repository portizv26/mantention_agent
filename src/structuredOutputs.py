
from typing import List, Optional
from pydantic import BaseModel


class messageClassification(BaseModel):
    """
    Class to define the actions required by the agent.
    
    Attributes:
        is_on_topic (bool): A flag to indicate whether the message is on topic or not.
        is_context_sufficient (bool): A flag to indicate whether the context is sufficient for the agent to respond.
    """
    is_on_topic: bool
    is_context_sufficient: bool
    
class actionsRequired(BaseModel):
    """
    Class to define the actions required by the agent.
    
    Attributes:
    
    """
    is_new_sql_query_needed: bool
    is_new_image_needed: bool