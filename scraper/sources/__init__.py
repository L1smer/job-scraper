from scraper.sources.jobspy_source import JobSpySource
from scraper.sources.justjoin import JustJoinSource
from scraper.sources.nofluffjobs import NoFluffJobsSource

SOURCES = {
    "justjoin": JustJoinSource,
    "nofluffjobs": NoFluffJobsSource,
    "jobspy": JobSpySource,
}
