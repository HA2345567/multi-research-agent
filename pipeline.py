from agents import build_reader_agent , build_search_agent , writer_chain , critic_chain
import re

def run_research_pipeline(topic : str) -> dict:

    state = {}

    #search agent working 
    print("\n"+" ="*50)
    print("step 1 - search agent is working ...")
    print("="*50)

    search_agent = build_search_agent()
    search_result = search_agent.invoke({
        "messages" : [("user", f"Use web_search to find recent, reliable and detailed information about: {topic}. Include the source URLs in your answer.")]
    })
    source_records = "\n\n".join(
        message.content for message in search_result["messages"]
        if getattr(message, "name", None) == "web_search"
        and isinstance(message.content, str)
    )
    source_urls = list(dict.fromkeys(
        re.findall(r"^URL:\s*(https?://\S+)", source_records, re.MULTILINE)
    ))
    if not source_urls:
        raise RuntimeError("Search did not return usable source URLs. Please retry the research.")
    source_list = "\n".join(source_urls)
    state["search_results"] = (
        search_result["messages"][-1].content
        + "\n\nSearch tool source records:\n" + source_records
    )

    print("\n search result ",state['search_results'])

    #step 2 - reader agent 
    print("\n"+" ="*50)
    print("step 2 - Reader agent is scraping top resources ...")
    print("="*50)

    reader_agent = build_reader_agent()
    reader_result = reader_agent.invoke({
        "messages": [("user",
            f"Research topic: {topic}\n\n"
            f"Choose the most relevant source from these exact URLs:\n{source_list}\n\n"
            "You must call scrape_url and base your answer on the returned page content. "
            "If a URL fails, try another URL from the list. Do not invent URLs or claim "
            "to have read a page without a successful tool result. Include the URL you read.\n\n"
            f"Search Results:\n{state['search_results']}"
        )]
    })

    read_calls = {
        call["id"]: call.get("args", {}).get("url", "")
        for message in reader_result["messages"]
        for call in getattr(message, "tool_calls", [])
        if call.get("name") == "scrape_url"
    }
    page_excerpts = [
        "URL: " + read_calls.get(getattr(message, "tool_call_id", ""), "")
        + "\n" + message.content
        for message in reader_result["messages"]
        if getattr(message, "name", None) == "scrape_url"
        and isinstance(message.content, str)
        and message.content.strip()
        and not message.content.lstrip().lower().startswith("could not scrape")
    ]
    if not page_excerpts:
        raise RuntimeError(
            "The reader did not successfully scrape any source. "
            "No report was generated from unverified reader output. Please retry."
        )
    state['scraped_content'] = (
        reader_result["messages"][-1].content
        + "\n\nRead source excerpts:\n" + "\n\n".join(page_excerpts)
    )

    print("\nscraped content: \n", state['scraped_content'])

    #step 3 - writer chain 

    print("\n"+" ="*50)
    print("step 3 - Writer is drafting the report ...")
    print("="*50)

    research_combined = (
        f"SEARCH RESULTS : \n {state['search_results']} \n\n"
        f"DETAILED SCRAPED CONTENT : \n {state['scraped_content']}"
    )

    state["report"] = writer_chain.invoke({
        "topic" : topic,
        "research" : research_combined
    })

    print("\n Final Report\n",state['report'])

    #critic report 

    print("\n"+" ="*50)
    print("step 4 - critic is reviewing the report ")
    print("="*50)

    state["feedback"] = critic_chain.invoke({
        "report":state['report']
    })

    print("\n critic report \n", state['feedback'])

    return state



if __name__ == "__main__":
    topic = input("\n Enter a research topic : ")
    run_research_pipeline(topic)
