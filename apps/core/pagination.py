class QuerystringMixin:
    """ListView uchun: sahifa raqamisiz GET parametrlar (`querystring`) — pagination havolalarida."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        context["querystring"] = params.urlencode()
        return context
