"""
Custom exception handling for production-ready error responses.
"""
import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, OperationalError

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom exception handler for all API views.
    Provides structured error responses with proper logging.
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)
    
    # Get request info for logging
    request = context.get('view').request if context.get('view') else None
    path = request.path if request else 'unknown'
    method = request.method if request else 'unknown'
    
    # Handle database errors
    if isinstance(exc, (DatabaseError, OperationalError)):
        logger.error(f"Database error at {method} {path}: {str(exc)}", exc_info=True)
        return Response(
            {
                'error': 'Database error occurred',
                'detail': 'Please try again later',
                'code': 'database_error'
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    
    # Handle Django validation errors
    if isinstance(exc, DjangoValidationError):
        logger.warning(f"Validation error at {method} {path}: {str(exc)}")
        return Response(
            {
                'error': 'Validation error',
                'detail': str(exc),
                'code': 'validation_error'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Handle unhandled exceptions
    if response is None:
        logger.error(
            f"Unhandled exception at {method} {path}: {type(exc).__name__}: {str(exc)}",
            exc_info=True
        )
        return Response(
            {
                'error': 'Internal server error',
                'detail': 'An unexpected error occurred',
                'code': 'internal_error'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    # Enhance existing response with structured data
    if hasattr(response, 'data'):
        # Add error code if not present
        if isinstance(response.data, dict) and 'code' not in response.data:
            response.data['code'] = get_error_code(exc)
        
        # Log the error
        if response.status_code >= 500:
            logger.error(f"Server error at {method} {path}: {exc}", exc_info=True)
        elif response.status_code >= 400:
            logger.warning(f"Client error at {method} {path}: {exc}")
    
    return response


def get_error_code(exc):
    """Get error code from exception type."""
    exc_name = type(exc).__name__.lower()
    return exc_name.replace('error', '').replace('exception', '') or 'unknown_error'
