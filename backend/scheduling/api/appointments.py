"""BB-21 booking endpoints: POST/GET /api/v1/appointments. The donor comes from the session only."""
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.parsers import StrictJSONParser
from scheduling.permissions import require_camp_role
from scheduling.selectors.appointments import list_own_appointments
from scheduling.services.appointments import book_appointment, cancel_appointment
from scheduling.slot_inputs import (AppointmentCancelInputSerializer, AppointmentCreateInputSerializer,
                                    AppointmentListInputSerializer)


@method_decorator(csrf_protect, name='dispatch')
class AppointmentCollectionView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def get(self, request, *args, **kwargs):
        require_camp_role(user=request.user, action='own_appointments')
        query = AppointmentListInputSerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        return Response(list_own_appointments(
            actor=request.user, page=params['page'], page_size=params['page_size']))

    def post(self, request, *args, **kwargs):
        require_camp_role(user=request.user, action='book')
        serializer = AppointmentCreateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(book_appointment(
            actor=request.user, slot_id=serializer.validated_data['slot_id']), status=201)


@method_decorator(csrf_protect, name='dispatch')
class AppointmentCancelView(APIView):
    """POST /api/v1/appointments/<id>/cancel, empty JSON object body."""
    permission_classes = [IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def post(self, request, appointment_id, *args, **kwargs):
        require_camp_role(user=request.user, action='cancel_own')
        AppointmentCancelInputSerializer(data=request.data).is_valid(raise_exception=True)
        return Response(cancel_appointment(actor=request.user, appointment_id=appointment_id))
