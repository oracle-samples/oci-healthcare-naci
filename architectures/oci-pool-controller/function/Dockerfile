# Controller-only image. The lab HTML and worker-side demo scripts are not copied.
# Pin these base-image tags to approved digests in your platform's build pipeline.
ARG FDK_BUILD_IMAGE=docker.io/fnproject/python:3.11-dev
ARG FDK_RUN_IMAGE=docker.io/fnproject/python:3.11
FROM ${FDK_BUILD_IMAGE} AS build-stage
WORKDIR /function
COPY requirements.txt /function/
RUN pip3 install --target /python --no-cache-dir -r requirements.txt
COPY func.py /function/

FROM ${FDK_RUN_IMAGE}
WORKDIR /function
COPY --from=build-stage /function /function
COPY --from=build-stage /python /python
ENV PYTHONPATH=/python
ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]
