FROM python:3.11.9-slim-bookworm@sha256:8fb099199b9f2d70342674bd9dbccd3ed03a258f26bbd1d556822c6dfc60c317

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

COPY controller_image/requirements.txt /tmp/controller-requirements.txt
RUN python -m pip install \
      --no-cache-dir \
      --requirement /tmp/controller-requirements.txt \
    && rm -f /tmp/controller-requirements.txt

ENTRYPOINT ["python"]
