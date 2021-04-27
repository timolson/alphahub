FROM python:3-alpine
RUN mkdir /alphahub
ENV PYTHONPATH /alphahub/src
WORKDIR /alphahub
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
COPY src .
ENTRYPOINT ["/usr/local/bin/python","-m","alphahub"]
